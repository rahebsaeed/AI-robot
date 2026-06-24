---
Page: Brain LLM Response System
Database: PFE Robot AI Documentation
Area: AI
Status: Ready
Tags: llm, qwen, prompt, response
Related Files: core/brain.py, main.py
---

# Brain LLM Response System

`core/brain.py` loads Qwen/Qwen2.5-1.5B-Instruct and returns structured robot decisions.

## Model

| Item | Value |
| --- | --- |
| Default model | `Qwen/Qwen2.5-1.5B-Instruct` |
| Device | CUDA if available, otherwise CPU |
| Precision | `bfloat16` on CUDA, `float32` on CPU |
| Loading mode | local files only |
| History | rolling 4-turn conversation window |

## Output Contract

The brain must return JSON:

```json
{
  "thought": "reason",
  "speech": "spoken reply",
  "action": {
    "move": "stop",
    "speed": 0.0,
    "arm": "home"
  }
}
```

Valid movement values:

```text
forward, backward, left, right, stop
```

Valid arm values:

```text
searching, pickup, drop, home
```

## Fast Intent Classifier

Before calling the LLM, the brain checks fast regex intents for:

- stop
- forward
- backward
- turn left
- turn right
- pickup
- drop
- hello
- how are you

This saves time on Jetson and avoids unnecessary LLM latency for common commands.

## Context Injection

`main.py` passes compact context:

- current map name
- AMCL pose
- navigation goal
- saved places
- camera summary
- lidar front distance
- status reminder that sensors exist

The prompt explicitly tells the model not to claim it has no sensors when context is provided.

## Safety After LLM

`main.py` does not trust raw model movement blindly. `build_safe_action()`:

- stops movement for chat/questions
- applies deterministic movement intents
- uses lidar to block forward motion
- caps speed
- defaults arm to `home` unless requested
- answers current camera questions directly from vision data

## Known Risks

- Small local model may answer weakly for broad intelligence questions.
- JSON may be malformed; parser normalizes output.
- Physical facts must be handled by deterministic code, not by the LLM.

## Images To Add In Notion

- Screenshot of `[BRAIN RAW]`, `[AI SPEECH]`, and `[ACTION]` logs.

