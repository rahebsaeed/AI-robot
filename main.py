import os
import json
import re
import cv2
from core.brain import Brain
from core.perceptions import Perceptions
from core.robot import Robot

def extract_json(text):
    """Robust extraction of JSON from LLM markdown response."""
    match = re.search(r'\{.*\}', text, re.DOTALL)
    return match.group(0) if match else text

def main():
    brain = Brain(model_name="Qwen/Qwen2.5-0.5B-Instruct")
    perceptions = Perceptions()
    robot = Robot()

    print("--- 1.5B SYSTEM ACTIVE ---")

    while True:
        try:
            frame, objects = perceptions.see()
            command = perceptions.listen()
            
            if command:
                print(f"User: {command}")
                raw_response, latency = brain.process_command(command, vision_data=objects)
                
                try:
                    json_str = extract_json(raw_response)
                    data = json.loads(json_str)
                    
                    # Feedback
                    perceptions.speak(data.get('speech', ''))
                    
                    # Physical Action
                    act = data.get('action', {})
                    robot.move(act.get('move', 'stop'), act.get('speed', 0.5))
                    robot.control_arm(act.get('arm', 'home'))
                    
                except Exception as e:
                    print(f"Parse Error: {e} | Raw: {raw_response}")

            if cv2.waitKey(1) & 0xFF == ord('q'): break
        except KeyboardInterrupt: break

if __name__ == '__main__':
    main()