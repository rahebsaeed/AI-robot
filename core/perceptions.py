import os
import sys

# CRITICAL WORKAROUND: Mask 'coverage' in the system module registry.
# This prevents numba from importing an incompatible version of the coverage library 
# and crashing with "AttributeError: module 'coverage' has no attribute 'types'".
sys.modules['coverage'] = None

import cv2
import speech_recognition as sr
import torch
import tempfile
import whisper
from ctypes import CFUNCTYPE, c_char_p, c_int, cdll
from contextlib import contextmanager

# Silence ALSA logs
def py_error_handler(filename, line, function, err, fmt):
    pass

ERROR_HANDLER_FUNC = CFUNCTYPE(None, c_char_p, c_int, c_char_p, c_int, c_char_p)
c_error_handler = ERROR_HANDLER_FUNC(py_error_handler)

try:
    asound = cdll.LoadLibrary('libasound.so.2')
    asound.snd_lib_error_set_handler(c_error_handler)
except Exception:
    pass

@contextmanager
def ignore_stderr():
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
    def __init__(self):
        # Audio setup
        self.recognizer = sr.Recognizer()
        self.recognizer.dynamic_energy_threshold = False
        self.recognizer.energy_threshold = 400
        
        # Diagnostics to choose correct mic routing
        mics = sr.Microphone.list_microphone_names()
        target_idx = None
        for i, name in enumerate(mics):
            if "pulse" in name.lower() or "default" in name.lower():
                target_idx = i
                break

        with ignore_stderr():
            if target_idx is not None:
                self.mic = sr.Microphone(device_index=target_idx, sample_rate=16000)
            else:
                self.mic = sr.Microphone(sample_rate=16000)
                
            print("Loading Whisper (tiny.en)...")
            self.whisper_model = whisper.load_model("tiny.en")
            
        self.cap = cv2.VideoCapture(0)

    def listen(self):
        with ignore_stderr():
            with self.mic as source:
                try:
                    audio = self.recognizer.listen(source, timeout=5, phrase_time_limit=5)
                    with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as tmp:
                        tmp.write(audio.get_wav_data())
                        tmp.flush()
                        result = self.whisper_model.transcribe(tmp.name, language="en")
                        return result["text"].strip()
                except Exception:
                    return None

    def see(self):
        ret, frame = self.cap.read()
        return (frame, self.detect_objects(frame)) if ret else (None, [])

    def detect_objects(self, frame):
        # Basic visual detection labels placeholder
        return ["table", "person"]

    def speak(self, text):
        """Converts text to speech and pipes it to the robot audio interface"""
        if not text: 
            return
        print(f"[ROBOT VOICE]: {text}")
        try:
            # -D hw:3,0 is target for standard USB setups on Jetson boards. 
            clean_text = text.replace('"', '').replace("'", "")
            os.system(f'espeak-ng -v en-us "{clean_text}" --stdout | aplay -D hw:3,0 > /dev/null 2>&1')
        except Exception as e:
            print(f"TTS Speaker Error: {e}")

    def __del__(self):
        if hasattr(self, 'cap'):
            self.cap.release()