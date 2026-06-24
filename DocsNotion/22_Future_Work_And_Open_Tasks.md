---
Page: Future Work And Open Tasks
Database: PFE Robot AI Documentation
Area: Roadmap
Status: Ready
Tags: future-work, backlog, improvements
Related Files: all
---

# Future Work And Open Tasks

## High Priority

| Task | Reason |
| --- | --- |
| Test Yahboom MakerControl on robot external Wi-Fi | Need exact robot-side failure data. |
| Fix head camera for default app | Likely `/dev/video*` reorder or hardcoded camera index. |
| Verify auto scan speed in several rooms | Needs physical safety and map quality validation. |
| Confirm AMCL startup in charge position | Fresh localization must converge without old pose. |
| Record final demo runbook | Needed for reproducible project presentation. |

## Medium Priority

| Task | Reason |
| --- | --- |
| Add stable udev camera aliases | Prevent head/arm camera swap after reboot. |
| Add Notion screenshots | Makes documentation presentation-ready. |
| Add more Android connection diagnostics | Better UX when wrong IP/token/port. |
| Improve open-vocabulary search | Search for non-COCO objects like door or colored objects. |
| Add map selection UI | Avoid hardcoded active map. |

## Low Priority

| Task | Reason |
| --- | --- |
| Refactor backup files | Reduce repo noise after final stable version. |
| Add package installer script | Easier deployment on fresh Jetson image. |
| Add structured log parser | Faster debugging from `/tmp/ai_companion.log`. |
| Add camera preview in custom Android app | Useful but heavier network/CPU load. |

## Research Directions

- Better semantic place memory.
- Door/corridor detection from map geometry.
- Safer autonomous exploration policies.
- Better natural-language grounding for robot facts.
- Voice wake word instead of manual mic enable.

## Images To Add In Notion

- Roadmap board screenshot after importing into Notion.

