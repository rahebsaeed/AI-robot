import torch
import time
import os
import gc
from transformers import AutoModelForCausalLM, AutoTokenizer

class Brain:
    def __init__(self, model_name="Qwen/Qwen2.5-0.5B-Instruct"):
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        
        # Optimization for Jetson Unified Memory
        os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"
        
        try:
            import transformers.modeling_utils
            transformers.modeling_utils.caching_allocator_warmup = lambda *args, **kwargs: None
        except Exception: pass

        print(f"Loading {model_name}...")
        self.tokenizer = AutoTokenizer.from_pretrained(model_name)
        
        load_args = {
            "pretrained_model_name_or_path": model_name,
            "device_map": "auto",
            "torch_dtype": torch.float16 if self.device == "cuda" else torch.bfloat16,
            "trust_remote_code": True,
        }

        gc.collect()
        if self.device == "cuda":
            torch.cuda.empty_cache()

        self.model = AutoModelForCausalLM.from_pretrained(**load_args)
        print("Brain ready.")

    def process_command(self, user_command, vision_data=None):
        start_time = time.time()
        vision_context = f"Objects detected: {', '.join(vision_data) if vision_data else 'none'}."
        
        system_prompt = (
            "You are a Rosmaster X3 PLUS Robot. Respond ONLY in valid JSON.\n"
            f"Context: {vision_context}\n"
            "Capabilities: move (forward/backward/left/right/stop), arm (searching/pickup/drop/home).\n"
            "Format:\n"
            "{\"thought\": \"...\", \"speech\": \"...\", \"action\": {\"move\": \"...\", \"speed\": 0.5, \"arm\": \"...\"}}"
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_command}
        ]
        
        input_ids = self.tokenizer.apply_chat_template(messages, tokenize=True, add_generation_prompt=True, return_tensors="pt").to(self.device)
        
        output_ids = self.model.generate(
            input_ids, 
            max_new_tokens=256, 
            do_sample=False, # Deterministic for robotics
            pad_token_id=self.tokenizer.pad_token_id
        )
        
        response = self.tokenizer.decode(output_ids[0][len(input_ids[0]):], skip_special_tokens=True)
        return response, time.time() - start_time
