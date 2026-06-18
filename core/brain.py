"""
brain.py — Upgraded AI decision brain for Rosmaster X3 PLUS
============================================================
Improvements over v1:
 - Structured few-shot system prompt is now SHORTER → faster inference on Jetson
 - Conversation memory (rolling 4-turn window) so the robot remembers context
 - Intent pre-classifier runs BEFORE the LLM on cheap regex, bypassing the model
   for ~80% of routine commands (stop, move, greet) → near-zero latency fallback
 - Vision summary injected as a compact token string instead of a verbose sentence
 - JSON extraction is more robust and never raises
 - Async warmup ping on first import so the model is JIT-compiled when user speaks
"""

import torch
import time
import os
import gc
import json
import re
import threading
from collections import deque
from transformers import AutoModelForCausalLM, AutoTokenizer


# ──────────────────────────────────────────────
# Fast intent pre-classifier  (no LLM needed)
# ──────────────────────────────────────────────

_FAST_INTENTS = [
    # (regex pattern, move, speed, arm, speech)
    (r"\b(stop|halt|freeze|don'?t move|do not move)\b",
     "stop", 0.0, "home", "Stopping now."),
    (r"\b(forward|go ahead|move ahead|advance)\b",
     "forward", 0.25, "home", "Moving forward."),
    (r"\b(backward|reverse|go back|move back)\b",
     "backward", 0.20, "home", "Moving backward."),
    (r"^(left)$|\b(turn|go|move|rotate)\s+left\b",
     "left", 0.22, "home", "Turning left."),
    (r"^(right)$|\b(turn|go|move|rotate)\s+right\b",
     "right", 0.22, "home", "Turning right."),
    (r"\b(pick up|pickup|grab|take|hold)\b",
     "stop", 0.0, "pickup", "Attempting to pick up."),
    (r"\b(drop|release|put down)\b",
     "stop", 0.0, "drop", "Dropping the object."),
    (r"\b(hello|hi|hey|how are you|what'?s up)\b",
     "stop", 0.0, "home", "Hello! I am ready to help you."),
]


def fast_classify(command: str):
    """
    Returns a pre-built response dict if the command matches a trivial intent,
    or None if the LLM should handle it.
    """
    t = command.lower().strip()
    for pattern, move, speed, arm, speech in _FAST_INTENTS:
        if re.search(pattern, t):
            return {
                "thought": f"fast-classified: {pattern}",
                "speech": speech,
                "action": {"move": move, "speed": speed, "arm": arm}
            }
    return None


# ──────────────────────────────────────────────
# Brain
# ──────────────────────────────────────────────

class Brain:
    def __init__(self, model_name="Qwen/Qwen2.5-1.5B-Instruct"):
        os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"

        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self._history: deque = deque(maxlen=8)   # 4 turns = 8 messages

        print(f"[BRAIN] Loading {model_name} on {self.device}…")
        if self.device == "cuda":
            print(f"[BRAIN] GPU: {torch.cuda.get_device_name(0)}")

        self.tokenizer = AutoTokenizer.from_pretrained(
            model_name, trust_remote_code=True, local_files_only=True
        )
        if self.tokenizer.pad_token_id is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token

        gc.collect()
        if self.device == "cuda":
            torch.cuda.empty_cache()

        dtype = torch.bfloat16 if self.device == "cuda" else torch.float32
        load_args = dict(
            pretrained_model_name_or_path=model_name,
            dtype=dtype,
            trust_remote_code=True,
            attn_implementation="eager",
            local_files_only=True,
        )
        if self.device == "cuda":
            load_args["device_map"] = {"": 0}

        self.model = AutoModelForCausalLM.from_pretrained(**load_args)
        if self.device == "cpu":
            self.model.to("cpu")
        self.model.eval()

        print(f"[BRAIN] Ready  dtype={dtype}")

        # Async warm-up so first real query is fast
        threading.Thread(target=self._warmup, daemon=True).start()

    # ── warm-up ──────────────────────────────
    def _warmup(self):
        try:
            self._call_llm("Hello", "none", "unknown")
            print("[BRAIN] Warm-up done.")
        except Exception as e:
            print(f"[BRAIN] Warm-up skipped: {e}")

    # ── system prompt (compact) ───────────────
    @staticmethod
    def _build_system(objects_text: str, lidar_text: str) -> str:
        return (
            "You are the brain of a Rosmaster X3 PLUS robot.\n"
            "Reply ONLY with this JSON (no markdown):\n"
            '{"thought":"<reason>","speech":"<spoken reply>","action":{"move":"<move>","speed":<0.0-1.0>,"arm":"<arm>"}}\n'
            "move: forward|backward|left|right|stop\n"
            "arm:  searching|pickup|drop|home\n\n"
            f"Camera: {objects_text}\n"
            f"Lidar:  {lidar_text}\n\n"
            "Rules (hard):\n"
            "1. move=stop, speed=0 for chat/questions.\n"
            "2. forward blocked if lidar<0.45 m.\n"
            "3. arm=home unless user explicitly asks to search/pick/drop.\n"
            "4. Unknown command → stop.\n\n"
            "Examples:\n"
            'User: hello → {"thought":"greeting","speech":"Hi! I am ready.","action":{"move":"stop","speed":0.0,"arm":"home"}}\n'
            'User: move forward → {"thought":"motion","speech":"Moving forward.","action":{"move":"forward","speed":0.25,"arm":"home"}}\n'
            'User: what do you see → {"thought":"vision","speech":"I see: OBJECTS","action":{"move":"stop","speed":0.0,"arm":"home"}}\n'
        )

    # ── compact vision summary ────────────────
    @staticmethod
    def _compact_vision(vision_data) -> str:
        if not vision_data:
            return "none"
        if isinstance(vision_data, dict):
            objs = vision_data.get("objects", [])
            if not objs:
                return "none"
            parts = []
            for o in objs[:4]:
                parts.append(f"{o.get('name','?')}@{o.get('position','?')}({o.get('distance_hint','?')})")
            return ", ".join(parts)
        if isinstance(vision_data, list):
            return ", ".join(str(x) for x in vision_data[:4]) or "none"
        return "none"

    # ── LLM call ─────────────────────────────
    def _call_llm(self, user_command: str, objects_text: str, lidar_text: str) -> str:
        system = self._build_system(objects_text, lidar_text)

        messages = [{"role": "system", "content": system}]
        messages.extend(self._history)
        messages.append({"role": "user", "content": user_command})

        text = self.tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        inputs = self.tokenizer([text], return_tensors="pt")
        inputs = {k: v.to(self.model.device) for k, v in inputs.items()}

        with torch.no_grad():
            output_ids = self.model.generate(
                **inputs,
                max_new_tokens=80,
                do_sample=False,
                repetition_penalty=1.12,
                no_repeat_ngram_size=3,
                pad_token_id=self.tokenizer.pad_token_id,
                eos_token_id=self.tokenizer.eos_token_id,
            )

        gen = output_ids[0][inputs["input_ids"].shape[1]:]
        return self.tokenizer.decode(gen, skip_special_tokens=True).strip()

    # ── JSON extraction ───────────────────────
    def _extract_json(self, raw: str) -> dict:
        raw = raw.strip().replace("```json", "").replace("```", "").strip()
        start, end = raw.find("{"), raw.rfind("}")
        if start != -1 and end > start:
            candidate = raw[start:end + 1]
            # balance braces
            while candidate.count("}") > candidate.count("{") and candidate.endswith("}"):
                candidate = candidate[:-1]
            try:
                return json.loads(candidate)
            except Exception:
                pass
        return {}

    # ── normalise output ──────────────────────
    @staticmethod
    def _normalise(data: dict) -> dict:
        thought = str(data.get("thought", ""))
        speech  = str(data.get("speech", "I am ready."))
        action  = data.get("action", {}) if isinstance(data.get("action"), dict) else {}

        move  = str(action.get("move", "stop")).lower()
        arm   = str(action.get("arm",  "home")).lower()

        aliases = {"ahead":"forward","front":"forward","forwards":"forward",
                   "back":"backward","reverse":"backward",
                   "turn_left":"left","turn_right":"right",
                   "stopped":"stop","none":"stop","idle":"stop"}
        move = aliases.get(move, move)

        if move not in {"forward","backward","left","right","stop"}:
            move = "stop"
        if arm not in {"searching","pickup","drop","home"}:
            arm = "home"

        try:
            speed = float(action.get("speed", 0.0))
        except Exception:
            speed = 0.0
        speed = max(0.0, min(1.0, speed))
        if move == "stop":
            speed = 0.0

        return {
            "thought": thought,
            "speech":  speech,
            "action":  {"move": move, "speed": speed, "arm": arm}
        }

    # ── public API ────────────────────────────
    def process_command(self, user_command: str, vision_data=None, lidar_distance=None):
        """
        Returns (response_dict, latency_seconds).
        response_dict always has thought / speech / action keys.
        """
        t0 = time.time()

        # 1. Fast pre-classifier – no LLM needed
        fast = fast_classify(user_command)
        if fast is not None:
            print(f"[BRAIN] fast-path: {fast['action']['move']}", flush=True)
            return fast, time.time() - t0

        # 2. Build context tokens
        objects_text = self._compact_vision(vision_data)
        lidar_text   = (f"{float(lidar_distance):.2f} m"
                        if lidar_distance is not None else "unknown")

        # 3. LLM
        raw = self._call_llm(user_command, objects_text, lidar_text)
        print(f"[BRAIN RAW]: {raw}", flush=True)

        data    = self._extract_json(raw)
        result  = self._normalise(data)

        # 4. Update rolling history (keep last 4 turns)
        self._history.append({"role": "user",      "content": user_command})
        self._history.append({"role": "assistant",  "content": result["speech"]})

        return result, time.time() - t0

    def clear_history(self):
        """Call this when a new task/search starts."""
        self._history.clear()