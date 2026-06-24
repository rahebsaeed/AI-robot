---
Page: Security Safety And Network Rules
Database: PFE Robot AI Documentation
Area: Security
Status: Ready
Tags: safety, security, network, stop
Related Files: core/mobile_gateway.py, core/mobile_control.py, start_navigation.sh, android-app/
---

# Security Safety And Network Rules

## Safety Principles

1. Stop must work even if AI is busy.
2. Forward motion requires lidar.
3. Robot microphone is muted by default.
4. Manual movement is dead-man controlled.
5. AMCL must be trusted before autonomous navigation.
6. Map saving must not overwrite static maps by accident.

## Emergency Stop Paths

| Source | Path |
| --- | --- |
| Android STOP | WebSocket `stop` message to `stop_all`. |
| Android text "stop" | Classified as emergency by `CommandBus`. |
| Face UI stop | UDP command to `UiCommandListener`. |
| Robot mic "stop" | Microphone producer calls emergency callback. |
| CTRL+C | Launcher cleanup stops robot and ROS. |

## Network Security

Custom Android app uses:

```text
ws://ROBOT_IP:8765
```

This is cleartext and must be used only on a trusted local network.

For deployment:

- use `wss://`
- use a real certificate
- use a short-lived pairing token
- do not expose robot ports to the internet
- firewall robot services where possible

## Token

Set token before launch:

```bash
export AI_MOBILE_TOKEN='replace-with-random-token'
./start_navigation.sh salle_robotique
```

If empty, the WebSocket is open to the local network.

## Movement Limits

Movement is limited at several levels:

- Android normalized speed `0.0` to `1.0`
- command bridge max linear/angular velocities
- `main.py` safe speed caps
- lidar obstacle checks
- mobile watchdog timeout

## Default MakerControl Security

MakerControl is Yahboom's default app. Its security and ports are not controlled by this custom project. When using it on external Wi-Fi, keep the network trusted and verify robot IP and ROS settings.

## Images To Add In Notion

- Diagram of all stop paths.
- Screenshot of Android STOP button.

