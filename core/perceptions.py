import os
import sys
import gc
import json
import queue
import re
import socket
import struct
import tempfile
import threading
import time
from contextlib import contextmanager
from ctypes import CFUNCTYPE, c_char_p, c_int, cdll
from urllib.parse import urlparse

# Workaround: mask "coverage" to prevent numba crashes on Jetson.
sys.modules["coverage"] = None

import cv2
import numpy as np
import speech_recognition as sr
import torch
import whisper
from ultralytics import YOLO


# Silence ALSA logs.
def _py_error_handler(filename, line, function, err, fmt):
    pass


ERROR_HANDLER_FUNC = CFUNCTYPE(None, c_char_p, c_int, c_char_p, c_int, c_char_p)
_c_error_handler = ERROR_HANDLER_FUNC(_py_error_handler)
try:
    asound = cdll.LoadLibrary("libasound.so.2")
    asound.snd_lib_error_set_handler(_c_error_handler)
except Exception:
    pass


@contextmanager
def _ignore_stderr():
    devnull = os.open(os.devnull, os.O_WRONLY)
    old_stderr = os.dup(sys.stderr.fileno())
    os.dup2(devnull, sys.stderr.fileno())
    try:
        yield
    finally:
        os.dup2(old_stderr, sys.stderr.fileno())
        os.close(devnull)
        os.close(old_stderr)


class Perceptions:
    LIDAR_UDP_PORT = 5010
    CAMERA_UDP_PORT = 5030
    CAMERA_STALE_AGE = 1.5

    def __init__(self):
        self._state_lock = threading.Lock()
        self._last_stt_error_time = 0.0
        self._last_stt_error_text = ""
        self._last_robot_speech_end = 0.0
        self.is_muted = False
        self._robot_moving = False
        self._search_target = None
        self._search_target_open_vocab = False
        self._last_yolo_time = 0.0
        self._last_vision_data = {
            "camera_ok": False,
            "description": "No camera frame yet.",
            "objects": [],
            "source": "udp_camera",
        }

        self.lidar_front = None
        self.lidar_all = None
        self.lidar_left = None
        self.lidar_right = None
        self.lidar_rear = None
        self._lidar_ts = 0.0

        self.latest_frame = None
        self.latest_frame_time = 0.0
        self.latest_frame_id = -1
        self.camera_frames_received = 0
        self._latest_frame_size = None

        self._start_lidar_listener()
        self._start_camera_listener()

        mics = sr.Microphone.list_microphone_names()
        print("Available Microphones:", mics, flush=True)

        env_idx = os.environ.get("ROBOT_MIC_INDEX")
        target_idx = None
        if env_idx is not None:
            try:
                target_idx = int(env_idx)
                print(f"Forcing Microphone Index from environment: {target_idx}", flush=True)
            except ValueError:
                pass

        if target_idx is None:
            for i, name in enumerate(mics):
                if "USB Microphone" in name:
                    target_idx = i
                    break
            if target_idx is None:
                for i, name in enumerate(mics):
                    if name == "default" or name == "pulse":
                        target_idx = i
                        break

        native_rate = 16000
        native_channels = 1
        if target_idx is not None:
            try:
                import pyaudio
                p = pyaudio.PyAudio()
                device_info = p.get_device_info_by_host_api_device_index(0, target_idx)
                native_rate = int(device_info.get("defaultSampleRate", 16000))
                native_channels = int(device_info.get("maxInputChannels", 1))
                p.terminate()
                print(f"Detected Native Settings for Device {target_idx}: {native_rate} Hz, {native_channels} channels", flush=True)
            except Exception as e:
                print(f"Failed to detect native settings: {e}. Using fallback 16000 Hz, 1 channel", flush=True)

        requested_channels = self._env_int("ROBOT_MIC_CHANNELS", 1, minimum=1, maximum=2)
        self.channels = min(requested_channels, max(1, native_channels))

        with _ignore_stderr():
            if target_idx is not None:
                print(f"Selecting Microphone Index: {target_idx} ({mics[target_idx]})", flush=True)
                self.mic = sr.Microphone(device_index=target_idx, sample_rate=native_rate)
                self.mic.CHANNELS = self.channels
            else:
                print("No clear USB Mic found, using default.", flush=True)
                self.mic = sr.Microphone()

            self.recognizer = sr.Recognizer()
            self.recognizer.dynamic_energy_threshold = True
            self.recognizer.dynamic_energy_adjustment_damping = self._env_float(
                "AI_MIC_DYNAMIC_DAMPING", 0.15, minimum=0.01, maximum=0.99
            )
            self.recognizer.dynamic_energy_ratio = self._env_float(
                "AI_MIC_DYNAMIC_RATIO", 1.8, minimum=1.05, maximum=4.0
            )
            self.recognizer.pause_threshold = self._env_float(
                "AI_MIC_PAUSE_SECONDS", 0.65, minimum=0.3, maximum=2.0
            )
            self.recognizer.phrase_threshold = self._env_float(
                "AI_MIC_PHRASE_SECONDS", 0.25, minimum=0.1, maximum=1.0
            )
            self.recognizer.non_speaking_duration = min(
                self.recognizer.pause_threshold,
                self._env_float("AI_MIC_NON_SPEAKING_SECONDS", 0.35, minimum=0.1, maximum=1.0),
            )
            configured_threshold = os.environ.get("AI_MIC_ENERGY_THRESHOLD", "").strip()
            self._manual_energy_threshold = False
            if configured_threshold:
                try:
                    self.recognizer.energy_threshold = max(1.0, float(configured_threshold))
                    self._manual_energy_threshold = True
                except ValueError:
                    print(f"[STT] Invalid AI_MIC_ENERGY_THRESHOLD={configured_threshold!r}; calibrating", flush=True)

            self._mic_recalibration_seconds = self._env_float(
                "AI_MIC_RECALIBRATION_SECONDS", 45.0, minimum=0.0, maximum=600.0
            )
            self._mic_quick_calibration_seconds = self._env_float(
                "AI_MIC_QUICK_CALIBRATION_SECONDS", 0.45, minimum=0.1, maximum=2.0
            )
            initial_calibration = self._env_float(
                "AI_MIC_CALIBRATION_SECONDS", 1.5, minimum=0.2, maximum=5.0
            )
            with self.mic as source:
                if self._manual_energy_threshold:
                    print(
                        f"[STT] Using fixed microphone threshold: {self.recognizer.energy_threshold:.2f}",
                        flush=True,
                    )
                else:
                    print(
                        f"Calibrating microphone for {initial_calibration:.1f} seconds... Please be quiet.",
                        flush=True,
                    )
                    self.recognizer.adjust_for_ambient_noise(source, duration=initial_calibration)
                    print(f"Calibration done. Threshold: {self.recognizer.energy_threshold:.2f}", flush=True)
            self._last_mic_calibration = time.monotonic()

            self._configure_stt()
            self.whisper_model, self.whisper_model_name, self.whisper_device = self._load_whisper_model()

        requested_model = os.environ.get("AI_YOLO_MODEL", "").strip()
        search_paths = []
        if requested_model:
            search_paths.append(requested_model)
        search_paths.extend([
            os.path.join(os.path.expanduser("~/AI"), "yolo11n.pt"),
            "yolo11n.pt",
        ])
        model_path = next((p for p in search_paths if p and os.path.exists(p)), None)
        if model_path is None:
            model_path = "yolo11n.pt"
        if requested_model and requested_model != model_path:
            print(f"[YOLO11] Requested model missing: {requested_model}; using {model_path}", flush=True)
        print(f"[YOLO11] Loading from: {model_path}", flush=True)
        self.yolo_model = YOLO(model_path)
        self._yolo_conf = float(os.environ.get("AI_YOLO_CONF", "0.25"))
        self._yolo_strict_min_conf = {
            "refrigerator": float(os.environ.get("AI_YOLO_REFRIGERATOR_CONF", "0.55")),
        }
        print("[YOLO11] Ready.", flush=True)

    @staticmethod
    def _env_float(name, default, minimum=None, maximum=None):
        try:
            value = float(os.environ.get(name, default))
        except (TypeError, ValueError):
            value = float(default)
        if minimum is not None:
            value = max(float(minimum), value)
        if maximum is not None:
            value = min(float(maximum), value)
        return value

    @staticmethod
    def _env_int(name, default, minimum=None, maximum=None):
        try:
            value = int(os.environ.get(name, default))
        except (TypeError, ValueError):
            value = int(default)
        if minimum is not None:
            value = max(int(minimum), value)
        if maximum is not None:
            value = min(int(maximum), value)
        return value

    def _configure_stt(self):
        self._stt_language = os.environ.get("AI_WHISPER_LANGUAGE", "en").strip() or "en"
        self._stt_beam_size = self._env_int("AI_WHISPER_BEAM_SIZE", 5, minimum=1, maximum=10)
        self._stt_no_speech_threshold = self._env_float(
            "AI_WHISPER_NO_SPEECH_THRESHOLD", 0.60, minimum=0.0, maximum=1.0
        )
        self._stt_logprob_threshold = self._env_float(
            "AI_WHISPER_LOGPROB_THRESHOLD", -1.0, minimum=-5.0, maximum=0.0
        )
        self._stt_compression_threshold = self._env_float(
            "AI_WHISPER_COMPRESSION_RATIO_THRESHOLD", 2.4, minimum=1.0, maximum=10.0
        )
        self._stt_reject_no_speech = self._env_float(
            "AI_STT_REJECT_NO_SPEECH", 0.72, minimum=0.0, maximum=1.0
        )
        self._stt_reject_logprob = self._env_float(
            "AI_STT_REJECT_LOGPROB", -1.15, minimum=-5.0, maximum=0.0
        )
        self._stt_min_rms = self._env_float("AI_STT_MIN_RMS", 35.0, minimum=0.0, maximum=5000.0)
        self._stt_min_audio_seconds = self._env_float(
            "AI_STT_MIN_AUDIO_SECONDS", 0.25, minimum=0.05, maximum=2.0
        )
        self._stt_echo_cooldown = self._env_float(
            "AI_STT_ECHO_COOLDOWN", 0.45, minimum=0.0, maximum=3.0
        )
        self._stt_initial_prompt = os.environ.get("AI_WHISPER_INITIAL_PROMPT", "").strip()

    @staticmethod
    def _cuda_free_gib():
        if not torch.cuda.is_available():
            return 0.0
        try:
            free_bytes, _ = torch.cuda.mem_get_info()
            return float(free_bytes) / (1024.0 ** 3)
        except Exception:
            return 0.0

    @staticmethod
    def _whisper_cache_root():
        configured = os.environ.get("AI_WHISPER_CACHE", "").strip()
        return os.path.expanduser(configured or "~/.cache/whisper")

    def _whisper_cached_path(self, model_name):
        cache_root = self._whisper_cache_root()
        filenames = [f"{model_name}.pt"]
        model_url = getattr(whisper, "_MODELS", {}).get(model_name, "")
        if model_url:
            url_filename = os.path.basename(urlparse(model_url).path)
            if url_filename and url_filename not in filenames:
                filenames.insert(0, url_filename)

        # Reject obvious partial downloads. Official checkpoints are much
        # larger than these conservative lower bounds.
        lower_name = model_name.lower()
        if lower_name.startswith("tiny"):
            minimum_bytes = 65 * 1024**2
        elif lower_name.startswith("base"):
            minimum_bytes = 125 * 1024**2
        elif lower_name.startswith("small"):
            minimum_bytes = 420 * 1024**2
        elif lower_name.startswith("medium"):
            minimum_bytes = 1300 * 1024**2
        elif "turbo" in lower_name:
            minimum_bytes = 1400 * 1024**2
        else:
            minimum_bytes = 2500 * 1024**2

        for filename in filenames:
            path = os.path.join(cache_root, filename)
            try:
                if os.path.isfile(path) and os.path.getsize(path) >= minimum_bytes:
                    return path
            except OSError:
                continue
        return None

    def _whisper_model_candidates(self):
        requested = os.environ.get("AI_WHISPER_MODEL", "auto").strip() or "auto"
        if requested.lower() != "auto":
            ordered = [requested, "small.en", "base.en", "tiny.en"]
        elif torch.cuda.is_available():
            free_gib = self._cuda_free_gib()
            if free_gib >= 7.0:
                ordered = ["turbo", "small.en", "base.en", "tiny.en"]
            elif free_gib >= 2.7:
                ordered = ["small.en", "base.en", "tiny.en"]
            elif free_gib >= 1.3:
                ordered = ["base.en", "tiny.en"]
            else:
                ordered = ["tiny.en"]
            print(f"[STT] Whisper auto-selection: CUDA free={free_gib:.1f} GiB", flush=True)
        else:
            ordered = ["base.en", "tiny.en"]

        available = set(whisper.available_models())
        result = []
        for name in ordered:
            if name not in available:
                print(f"[STT] Whisper model unavailable in installed package: {name}", flush=True)
                continue
            if name not in result:
                result.append(name)

        auto_download = os.environ.get("AI_WHISPER_AUTO_DOWNLOAD", "0").strip() == "1"
        if requested.lower() == "auto" and not auto_download:
            cached = [name for name in result if self._whisper_cached_path(name)]
            skipped = [name for name in result if name not in cached]
            if skipped:
                print(
                    f"[STT] Auto mode skipping uncached models: {', '.join(skipped)}",
                    flush=True,
                )
            if cached:
                result = cached
            else:
                print("[STT] No complete cached model found; using tiny.en fallback", flush=True)
                result = ["tiny.en"]
        if not result:
            result = ["tiny.en"]
        return result

    def _load_whisper_model(self):
        device_setting = os.environ.get("AI_WHISPER_DEVICE", "auto").strip().lower() or "auto"
        device = "cuda" if device_setting == "auto" and torch.cuda.is_available() else device_setting
        if device == "auto":
            device = "cpu"
        if device.startswith("cuda") and not torch.cuda.is_available():
            print("[STT] CUDA requested but unavailable; using CPU", flush=True)
            device = "cpu"

        errors = []
        cache_root = self._whisper_cache_root()
        for model_name in self._whisper_model_candidates():
            try:
                cached_path = self._whisper_cached_path(model_name)
                cache_state = cached_path or "download required"
                print(
                    f"[STT] Loading Whisper {model_name} on {device} ({cache_state})...",
                    flush=True,
                )
                model = whisper.load_model(model_name, device=device, download_root=cache_root)
                print(
                    f"[STT] Whisper ready: model={model_name} device={device} "
                    f"beam={self._stt_beam_size} language={self._stt_language}",
                    flush=True,
                )
                return model, model_name, device
            except Exception as exc:
                errors.append(f"{model_name}: {exc}")
                print(f"[STT] Could not load {model_name}: {exc}", flush=True)
                gc.collect()
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
        raise RuntimeError("No Whisper model could be loaded. " + " | ".join(errors))

    def _start_lidar_listener(self):
        def _listen():
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("0.0.0.0", self.LIDAR_UDP_PORT))
            print(f"[LIDAR] UDP listener on :{self.LIDAR_UDP_PORT}", flush=True)
            while True:
                try:
                    data, _ = sock.recvfrom(8192)
                    payload = json.loads(data.decode("utf-8", errors="replace"))
                    if not isinstance(payload, dict):
                        continue
                    front = payload.get("front")
                    all_dist = payload.get("all", payload.get("min_all", front))
                    with self._state_lock:
                        self.lidar_front = self._safe_float(front)
                        self.lidar_all = self._safe_float(all_dist)
                        self.lidar_left = self._safe_float(payload.get("left"))
                        self.lidar_right = self._safe_float(payload.get("right"))
                        self.lidar_rear = self._safe_float(payload.get("rear"))
                        self._lidar_ts = time.time()
                except Exception as e:
                    print(f"[LIDAR ERROR] {e}", flush=True)
                    time.sleep(0.2)

        threading.Thread(target=_listen, daemon=True).start()

    def _start_camera_listener(self):
        def _listen():
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("0.0.0.0", self.CAMERA_UDP_PORT))
            print(f"[CAMERA] UDP listener on :{self.CAMERA_UDP_PORT}", flush=True)
            while True:
                try:
                    data, _ = sock.recvfrom(65535)
                    if len(data) < 4:
                        continue
                    frame_id = struct.unpack("!I", data[:4])[0]
                    jpg = np.frombuffer(data[4:], dtype=np.uint8)
                    frame = cv2.imdecode(jpg, cv2.IMREAD_COLOR)
                    if frame is None:
                        continue
                    with self._state_lock:
                        self.latest_frame = frame
                        self.latest_frame_time = time.time()
                        self.latest_frame_id = frame_id
                        self.camera_frames_received += 1
                        self._latest_frame_size = (int(frame.shape[1]), int(frame.shape[0]))
                        if self.camera_frames_received % 100 == 0:
                            print(f"[CAMERA] {frame.shape[1]}x{frame.shape[0]} frames={self.camera_frames_received}", flush=True)
                except Exception as e:
                    print(f"[CAMERA ERROR] {e}", flush=True)
                    time.sleep(0.2)

        threading.Thread(target=_listen, daemon=True).start()

    def _safe_float(self, value):
        try:
            if value is None:
                return None
            value = float(value)
            if np.isnan(value) or np.isinf(value):
                return None
            return value
        except Exception:
            return None

    def set_mute(self, muted: bool):
        self.is_muted = bool(muted)
        print(f"[STT] Microphone {'MUTED' if self.is_muted else 'UNMUTED'}", flush=True)

    def set_moving(self, moving: bool):
        self._robot_moving = bool(moving)

    def set_search_target(self, target, open_vocab=False):
        self._search_target = target
        self._search_target_open_vocab = bool(open_vocab)

    @staticmethod
    def _audio_metrics(audio_data):
        raw = audio_data.get_raw_data(convert_rate=16000, convert_width=2)
        samples = np.frombuffer(raw, dtype=np.int16)
        if samples.size == 0:
            return 0.0, 0.0, 0
        samples_float = samples.astype(np.float32)
        rms = float(np.sqrt(np.mean(samples_float * samples_float)))
        peak = int(np.max(np.abs(samples_float)))
        duration = float(samples.size) / 16000.0
        return duration, rms, peak

    def _transcription_quality(self, result):
        segments = result.get("segments", []) if isinstance(result, dict) else []
        if not segments:
            return None, None, True

        weights = []
        no_speech_values = []
        logprob_values = []
        for segment in segments:
            try:
                duration = max(0.05, float(segment.get("end", 0.0)) - float(segment.get("start", 0.0)))
                no_speech = float(segment.get("no_speech_prob", 0.0))
                avg_logprob = float(segment.get("avg_logprob", 0.0))
            except (TypeError, ValueError):
                continue
            weights.append(duration)
            no_speech_values.append(no_speech)
            logprob_values.append(avg_logprob)

        if not weights:
            return None, None, True
        total = sum(weights)
        mean_no_speech = sum(v * w for v, w in zip(no_speech_values, weights)) / total
        mean_logprob = sum(v * w for v, w in zip(logprob_values, weights)) / total
        reject = (
            mean_logprob < self._stt_reject_logprob
            or (
                mean_no_speech >= self._stt_reject_no_speech
                and mean_logprob < -0.55
            )
        )
        return mean_no_speech, mean_logprob, reject

    def _wait_for_speaker_echo(self):
        remaining = self._stt_echo_cooldown - (time.monotonic() - self._last_robot_speech_end)
        if remaining > 0.0:
            time.sleep(remaining)

    def listen(self, timeout=5, phrase_time_limit=8):
        if self.is_muted:
            time.sleep(0.5)
            return None

        self._wait_for_speaker_echo()

        import speech_recognition as sr_lib

        with _ignore_stderr():
            try:
                with self.mic as source:
                    calibration_due = (
                        not self._manual_energy_threshold
                        and self._mic_recalibration_seconds > 0.0
                        and time.monotonic() - self._last_mic_calibration >= self._mic_recalibration_seconds
                    )
                    if calibration_due and not self._robot_moving:
                        print("[STT] Refreshing ambient-noise calibration...", flush=True)
                        self.recognizer.adjust_for_ambient_noise(
                            source, duration=self._mic_quick_calibration_seconds
                        )
                        self._last_mic_calibration = time.monotonic()
                    self.recognizer.dynamic_energy_threshold = True
                    print(f"\n[LISTENING] (threshold={self.recognizer.energy_threshold:.1f})...", flush=True)
                    audio_data = self.recognizer.listen(source, timeout=timeout, phrase_time_limit=phrase_time_limit)
            except sr_lib.WaitTimeoutError:
                return None
            except Exception as e:
                print(f"[LISTEN ERROR] {e}", flush=True)
                return None

        if self.is_muted:
            return None

        try:
            duration, rms, peak = self._audio_metrics(audio_data)
            print(
                f"[VOICE] Got audio: duration={duration:.2f}s rms={rms:.0f} peak={peak}; transcribing...",
                flush=True,
            )
            if duration < self._stt_min_audio_seconds or rms < self._stt_min_rms:
                print("[STT] Rejected audio before Whisper: too short or too quiet", flush=True)
                return None

            wav_data = audio_data.get_wav_data(convert_rate=16000, convert_width=2)
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_file:
                tmp_path = tmp_file.name
                tmp_file.write(wav_data)
            try:
                transcribe_options = {
                    "task": "transcribe",
                    "language": self._stt_language,
                    "fp16": self.whisper_device.startswith("cuda"),
                    "temperature": 0.0,
                    "beam_size": self._stt_beam_size,
                    "patience": 1.0,
                    "condition_on_previous_text": False,
                    "no_speech_threshold": self._stt_no_speech_threshold,
                    "logprob_threshold": self._stt_logprob_threshold,
                    "compression_ratio_threshold": self._stt_compression_threshold,
                    "word_timestamps": False,
                    "verbose": False,
                }
                if self._stt_initial_prompt:
                    transcribe_options["initial_prompt"] = self._stt_initial_prompt
                result = self.whisper_model.transcribe(tmp_path, **transcribe_options)
                text = str(result.get("text", "")).strip()
            finally:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass
            no_speech, avg_logprob, reject = self._transcription_quality(result)
            quality_text = (
                f"no_speech={no_speech:.2f} avg_logprob={avg_logprob:.2f}"
                if no_speech is not None and avg_logprob is not None
                else "quality=unavailable"
            )
            if reject:
                print(f"[STT] Rejected low-confidence transcription: {quality_text} text={text!r}", flush=True)
                return None
            if len(text) > 2 and re.search(r"[A-Za-z0-9]", text):
                print(f"[HEARD] \"{text}\" ({quality_text}, model={self.whisper_model_name})", flush=True)
                return text
            return None
        except Exception as e:
            print(f"[LISTEN ERROR] {e}", flush=True)
            return None

    def _detect_objects(self, frame):
        if frame is None:
            return []
        try:
            results = self.yolo_model(frame, imgsz=320, conf=self._yolo_conf, verbose=False)
        except Exception as e:
            print(f"[YOLO ERROR] {e}", flush=True)
            return []

        objects = []
        h, w = frame.shape[:2]
        for result in results:
            boxes = getattr(result, "boxes", None)
            if boxes is None:
                continue
            for box in boxes:
                try:
                    cls_id = int(box.cls[0])
                    conf = float(box.conf[0])
                    if hasattr(self.yolo_model, "names"):
                        name = str(self.yolo_model.names.get(cls_id, cls_id))
                    else:
                        name = str(cls_id)
                    if conf < self._min_yolo_conf_for(name):
                        continue
                    x1, y1, x2, y2 = map(float, box.xyxy[0].tolist())
                    bw = max(1.0, x2 - x1)
                    bh = max(1.0, y2 - y1)
                    cx = (x1 + x2) / 2.0
                    area = bw * bh
                    frame_area = float(w * h) if w and h else 1.0
                    rel = area / frame_area
                    if rel > 0.18:
                        distance_hint = "close"
                    elif rel > 0.08:
                        distance_hint = "medium"
                    else:
                        distance_hint = "far"
                    if cx < w * 0.33:
                        position = "left"
                    elif cx > w * 0.66:
                        position = "right"
                    else:
                        position = "center"
                    objects.append({
                        "name": name,
                        "confidence": round(conf, 3),
                        "bbox": [round(x1, 1), round(y1, 1), round(x2, 1), round(y2, 1)],
                        "position": position,
                        "distance_hint": distance_hint,
                        "source": "yolo11",
                    })
                except Exception:
                    continue
        return self._dedupe_objects(objects)

    def _dedupe_objects(self, objects):
        kept = []
        for obj in sorted(objects, key=lambda o: -float(o.get("confidence", 0.0))):
            bbox = obj.get("bbox")
            if not isinstance(bbox, list) or len(bbox) != 4:
                continue
            duplicate = False
            for existing in kept:
                if existing.get("name") != obj.get("name"):
                    continue
                if self._iou(existing.get("bbox"), bbox) > 0.55:
                    duplicate = True
                    break
            if not duplicate:
                kept.append(obj)
        return kept

    def _iou(self, b1, b2):
        if not b1 or not b2:
            return 0.0
        xa = max(float(b1[0]), float(b2[0]))
        ya = max(float(b1[1]), float(b2[1]))
        xb = min(float(b1[2]), float(b2[2]))
        yb = min(float(b1[3]), float(b2[3]))
        inter_w = max(0.0, xb - xa)
        inter_h = max(0.0, yb - ya)
        inter = inter_w * inter_h
        if inter <= 0:
            return 0.0
        area1 = max(1.0, (float(b1[2]) - float(b1[0])) * (float(b1[3]) - float(b1[1])))
        area2 = max(1.0, (float(b2[2]) - float(b2[0])) * (float(b2[3]) - float(b2[1])))
        return inter / max(1.0, area1 + area2 - inter)

    def _min_yolo_conf_for(self, name: str) -> float:
        name = str(name).strip().lower()
        return float(self._yolo_strict_min_conf.get(name, self._yolo_conf))

    def _build_vision_description(self, objects, camera_ok):
        if not camera_ok:
            return "No camera frame yet."
        if not objects:
            return "Camera active. No known objects detected right now."
        parts = []
        for o in objects[:6]:
            parts.append(
                f"{o.get('name', '?')} at {o.get('position', '?')} ({o.get('distance_hint', '?')}, {float(o.get('confidence', 0.0)):.2f})"
            )
        return "I detect: " + ", ".join(parts) + "."

    def see(self, force=False):
        with self._state_lock:
            frame = None if self.latest_frame is None else self.latest_frame.copy()
            frame_time = self.latest_frame_time
            objects = list(self._last_vision_data.get("objects", []))
            camera_ok = bool(frame is not None and (time.time() - frame_time) <= self.CAMERA_STALE_AGE)
            last_yolo = float(self._last_yolo_time)
            latest_size = self._latest_frame_size

        vision_data = dict(self._last_vision_data)
        vision_data["camera_ok"] = camera_ok
        if frame is not None and camera_ok:
            yolo_interval = 0.33 if self._robot_moving else 1.0
            if force or time.time() - last_yolo >= yolo_interval:
                objects = self._detect_objects(frame)
                vision_data["objects"] = objects
                vision_data["description"] = self._build_vision_description(objects, camera_ok=True)
                vision_data["source"] = "udp_camera"
                vision_data["frame_size"] = latest_size
                vision_data["frame_age"] = round(time.time() - frame_time, 3)
                with self._state_lock:
                    self._last_vision_data = dict(vision_data)
                    self._last_yolo_time = time.time()
            else:
                vision_data = dict(self._last_vision_data)
                vision_data["camera_ok"] = True
                vision_data["frame_size"] = latest_size
                vision_data["frame_age"] = round(time.time() - frame_time, 3)
            return frame, vision_data

        if frame is not None:
            vision_data["camera_ok"] = False
            vision_data.setdefault("objects", objects)
            vision_data["description"] = self._build_vision_description(objects, camera_ok=False)
            vision_data["source"] = "udp_camera_stale"
            return frame, vision_data

        vision_data["camera_ok"] = False
        vision_data["objects"] = []
        vision_data["description"] = "No camera frame yet."
        vision_data["source"] = "udp_camera"
        return None, vision_data

    def get_lidar_distance(self):
        with self._state_lock:
            if self.lidar_front is not None:
                return self.lidar_front
            return self.lidar_all

    def speak(self, text):
        if not text:
            return
        print(f"[ROBOT VOICE]: {text}", flush=True)
        try:
            import shlex
            cmd = f"espeak-ng -v en-us {shlex.quote(str(text))} --stdout | aplay -D hw:3,0 > /dev/null 2>&1"
            os.system(cmd)
        except Exception as e:
            print(f"Speaker Error: {e}", flush=True)
        finally:
            self._last_robot_speech_end = time.monotonic()
