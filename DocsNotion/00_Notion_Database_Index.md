---
Page: Notion Database Index
Database: PFE Robot AI Documentation
Area: Documentation
Status: Ready for Notion import
Tags: index, notion, documentation
---

# Notion Database Index

This folder is designed to be imported into Notion as one documentation database. Each markdown file is one database row/page. Use the file name as the Notion page name.

## Suggested Notion Database Properties

| Property | Type | Use |
| --- | --- | --- |
| Page | Title | Page name from the markdown file. |
| Area | Select | Architecture, Robot, AI, Android, Mapping, Operations, Troubleshooting, Testing, Security. |
| Status | Select | Draft, Ready, Needs Robot Test, Deprecated. |
| Priority | Select | High, Medium, Low. |
| Owner | Person | Person responsible for maintaining the page. |
| Related Files | Text | Source files referenced by the page. |
| Last Verified | Date | Date when tested on the real robot. |
| Images Needed | Checkbox | Mark true if screenshots/photos should be added. |

## Page List

| File | Page |
| --- | --- |
| `01_Project_Executive_Summary.md` | Project Executive Summary |
| `02_System_Architecture.md` | System Architecture |
| `03_Hardware_And_Network_Setup.md` | Hardware And Network Setup |
| `04_Robot_Side_File_Map.md` | Robot Side File Map |
| `05_Launch_Navigation_Start_Navigation.md` | Launch Navigation Start Navigation |
| `06_AMCL_Localization_And_Map_Pose.md` | AMCL Localization And Map Pose |
| `07_Automatic_Mapping_Auto_Scan.md` | Automatic Mapping Auto Scan |
| `08_Manual_Mapping_Keyboard_Scan.md` | Manual Mapping Keyboard Scan |
| `09_AI_Main_Loop_And_Command_Routing.md` | AI Main Loop And Command Routing |
| `10_Brain_LLM_Response_System.md` | Brain LLM Response System |
| `11_Perception_Camera_Lidar_Microphone_YOLO.md` | Perception Camera Lidar Microphone YOLO |
| `12_Robot_Control_MoveBase_CmdVel_RViz.md` | Robot Control MoveBase CmdVel RViz |
| `13_Search_Objects_And_Saved_Places.md` | Search Objects And Saved Places |
| `14_Mobile_Android_App_And_WebSocket_Protocol.md` | Mobile Android App And WebSocket Protocol |
| `15_Robot_Face_UI_And_Operator_Controls.md` | Robot Face UI And Operator Controls |
| `16_Default_Rosmaster_MakerControl_App.md` | Default Rosmaster MakerControl App |
| `17_Configuration_Environment_Variables.md` | Configuration Environment Variables |
| `18_Operations_Runbooks.md` | Operations Runbooks |
| `19_Troubleshooting_Known_Problems.md` | Troubleshooting Known Problems |
| `20_Testing_Validation_Quality.md` | Testing Validation Quality |
| `21_Security_Safety_And_Network_Rules.md` | Security Safety And Network Rules |
| `22_Future_Work_And_Open_Tasks.md` | Future Work And Open Tasks |
| `23_Glossary.md` | Glossary |
| `24_Image_And_Diagram_Checklist.md` | Image And Diagram Checklist |

## Import Instructions

1. Open Notion.
2. Create a page named `PFE Robot AI Documentation`.
3. Choose Import, then Markdown and CSV.
4. Import the markdown files from `DocsNotion/`.
5. Convert the imported pages into a database, or move them into a documentation database.
6. Add the suggested properties above.

## Images To Add In Notion

- Architecture diagram from `docs/algorithm_schema.svg`.
- Robot hardware photo with camera, lidar, arm, and Jetson labels.
- Android app screenshots for Map, Conversation, and Control tabs.
- RViz screenshot showing map, AMCL pose, lidar scan, costmaps, and global plan.
- Terminal screenshots for successful `start_navigation.sh` and `auto_scan.sh`.

