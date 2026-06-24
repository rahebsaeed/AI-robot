import sys
import os
import random
import threading
import time
import json

# Advanced: Environment setup for GDM/System contexts
if os.getuid() == 121 or "gdm" in os.environ.get("USER", ""):
    os.environ["QT_QPA_PLATFORM"] = "xcb"
    os.environ["XDG_RUNTIME_DIR"] = "/run/user/121"

from PyQt5.QtWidgets import QApplication, QMainWindow, QGraphicsView, QGraphicsScene, QGraphicsEllipseItem, QFrame, QGraphicsPathItem, QPushButton
from PyQt5.QtCore import Qt, QTimer, QPropertyAnimation, QRectF, QEasingCurve, pyqtProperty, QObject, QPointF, pyqtSignal, QThread
from PyQt5.QtGui import QColor, QBrush, QPen, QRadialGradient, QPainter, QPainterPath

# Helper class for animated graphics items
from PyQt5.QtWidgets import QGraphicsObject

class AnimatedEllipseItem(QGraphicsObject):
    def __init__(self, x, y, w, h, parent=None):
        super().__init__(parent)
        self.rect = QRectF(0, 0, w, h)
        self.setPos(x, y)
        self._brush = QBrush(Qt.white)
        self._pen = QPen(Qt.transparent)

    def boundingRect(self):
        return self.rect

    def paint(self, painter, option, widget):
        painter.setBrush(self._brush)
        painter.setPen(self._pen)
        painter.drawEllipse(self.rect)

    def setBrush(self, brush):
        self._brush = brush
        self.update()

    def setPen(self, pen):
        self._pen = pen
        self.update()

class EyeLid(QGraphicsObject):
    def __init__(self, x, y, w, h, is_top=True, parent=None):
        super().__init__(parent)
        self.w = w
        self.h = h
        self.is_top = is_top
        self.setPos(x, y)
        self._progress = 0.0 # 0=Open, 1=Closed
        self.brush = QBrush(QColor(10, 10, 15)) # Dark eyelid
        
    def boundingRect(self):
        return QRectF(0, 0, self.w, self.h)

    def paint(self, painter, option, widget):
        painter.setBrush(self.brush)
        painter.setPen(Qt.NoPen)
        path = QPainterPath()
        if self.is_top:
            # Drop from top
            h_reach = self.h * self._progress
            path.addRect(0, 0, self.w, h_reach)
        else:
            # Rise from bottom
            h_reach = self.h * self._progress
            path.addRect(0, self.h - h_reach, self.w, h_reach)
        painter.drawPath(path)

    @pyqtProperty(float)
    def progress(self): return self._progress
    @progress.setter
    def progress(self, val):
        self._progress = val
        self.update()



class Mouth(QGraphicsObject):
    def __init__(self, x, y, w, h, parent=None):
        super().__init__(parent)
        self.w = w
        self.h = h
        self.setPos(x, y)
        self._curve = 0.0 # -1.0 (Sad) to 1.0 (Happy), 0.0 (Neutral)
        self._open = 0.1 # 0.0 to 1.0
        self.brush = QBrush(QColor(0, 200, 255, 200))
        self.pen = QPen(QColor(0, 200, 255), 10, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)

    def boundingRect(self):
        return QRectF(-50, -50, self.w+100, self.h+100)

    def paint(self, painter, option, widget):
        painter.setPen(self.pen)
        path = QPainterPath()
        # Draw a curved line (Quadratic Bezier)
        # Center is (w/2, h/2)
        start_pt = QPointF(0, self.h/2)
        end_pt = QPointF(self.w, self.h/2)
        control_pt = QPointF(self.w/2, self.h/2 + self._curve * self.h)
        path.moveTo(start_pt)
        path.quadTo(control_pt, end_pt)
        painter.drawPath(path)

    @pyqtProperty(float)
    def curve(self): return self._curve
    @curve.setter
    def curve(self, val):
        self._curve = val
        self.update()

    def set_color(self, color):
        self.pen.setColor(color)
        self.update()

class SentimentAnalyzer:
    @staticmethod
    def get_emotion(text):
        text = text.lower()
        # English & French keywords
        if any(word in text for word in ["danger", "obstacle", "stop", "careful", "stuck", "attention", "peur", "bloqué"]):
            return "fear"
        if any(word in text for word in ["hello", "hi", "happy", "good", "nice", "success", "found", "bonjour", "salut", "heureux", "bien", "succès", "trouvé"]):
            return "happy"
        if any(word in text for word in ["sorry", "sad", "failed", "error", "lost", "empty", "désolé", "triste", "erreur", "perdu", "vide"]):
            return "sad"
        if any(word in text for word in ["question", "thinking", "calculate", "search", "where", "how", "pense", "réfléchir", "cherche", "comment", "où"]):
            return "thinking"
        if any(word in text for word in ["blocked", "move", "why", "again", "stupid", "colère", "énervé", "pourquoi"]):
            return "angry"
        return None

# Try to import rospy
try:
    import rospy
    from std_msgs.msg import String
    from geometry_msgs.msg import Twist
    from sensor_msgs.msg import LaserScan
    from actionlib_msgs.msg import GoalStatusArray
except ImportError:
    rospy = None

class CommunicationHandler(QObject):
    emotion_signal = pyqtSignal(str)
    message_signal = pyqtSignal(str)
    vel_signal = pyqtSignal(float, float)
    status_signal = pyqtSignal(int)
    scan_signal = pyqtSignal(float)
    search_status_signal = pyqtSignal(dict)

    def __init__(self):
        super().__init__()
        self.sentiment_analyzer = SentimentAnalyzer()
        self._ros_thread = None

    def start_ros_thread(self):
        if self._ros_thread is None:
            self._ros_thread = QThread()
            self.moveToThread(self._ros_thread)
            self._ros_thread.started.connect(self._ros_init)
            self._ros_thread.start()

    def _ros_init(self):
        if rospy is None:
            return
        try:
            rospy.init_node('robot_face_ui', anonymous=True)
            rospy.Subscriber("/move_base/status", GoalStatusArray, lambda m: self.status_signal.emit(m.status_list[-1].status if m.status_list else 0))
            rospy.Subscriber("/cmd_vel", Twist, lambda m: self.vel_signal.emit(m.linear.x, m.angular.z))
            def handle_msg(m):
                self.message_signal.emit(m.data)
                emotion = self.sentiment_analyzer.get_emotion(m.data)
                if emotion:
                    self.emotion_signal.emit(emotion)
            rospy.Subscriber("/ai_response", String, handle_msg)
            rospy.Subscriber("/scan", LaserScan, lambda m: self.scan_signal.emit(min(m.ranges) if m.ranges else 10.0))
            rospy.spin()
        except Exception as e:
            print(f"ROS Init Error: {e}")

    def start_listeners(self):
        self.start_ros_thread()
        threading.Thread(target=self._udp_thread, daemon=True).start()

    def _udp_thread(self):
        import socket
        print("DEBUG: UDP listener thread started, binding to 127.0.0.1:5005")
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(('127.0.0.1', 5005))
        while True:
            try:
                data, _ = sock.recvfrom(8192)
                raw = data.decode().strip()
                print("DEBUG: Received raw UDP payload:", raw)
                if raw.startswith("status:"):
                    try:
                        status = json.loads(raw[7:])
                    except Exception as e:
                        print("UDP status parse error:", e)
                        continue
                    if isinstance(status, dict):
                        self.search_status_signal.emit(status)
                elif raw.startswith("message:"):
                    msg_text = raw[8:]
                    self.message_signal.emit(msg_text)
                    emotion = self.sentiment_analyzer.get_emotion(msg_text)
                    if emotion:
                        self.emotion_signal.emit(emotion)
                elif raw.startswith("msg:"):
                    msg_text = raw[4:]
                    self.message_signal.emit(msg_text)
                    emotion = self.sentiment_analyzer.get_emotion(msg_text)
                    if emotion:
                        self.emotion_signal.emit(emotion)
                else:
                    self.emotion_signal.emit(raw)
            except Exception as e:
                print("UDP recv error:", e)

class Eye(QObject):
    def __init__(self, scene, x, y, size=200):
        super().__init__()
        self.scene = scene
        self.base_size = size
        self.x = x
        self.y = y
        
        # Eye outer part (Glowing)
        self.outer = AnimatedEllipseItem(x, y, size, size)
        self.scene.addItem(self.outer)
        
        # Pupil
        self.pupil_size = size * 0.45
        self.pupil = AnimatedEllipseItem(x + (size - self.pupil_size)/2, y + (size - self.pupil_size)/2, self.pupil_size, self.pupil_size)
        self.scene.addItem(self.pupil)

        # Advanced: Iris Glimmer (Depth Effect)
        self.iris_size = self.pupil_size * 0.7
        self.iris = AnimatedEllipseItem(x + (size - self.iris_size)/2, y + (size - self.iris_size)/2, self.iris_size, self.iris_size)
        self.iris.setOpacity(0.3)
        self.scene.addItem(self.iris)
        
        # Advanced: Mechanical Eyelids
        self.top_lid = EyeLid(x, y, size, size, is_top=True)
        self.bottom_lid = EyeLid(x, y, size, size, is_top=False)
        self.scene.addItem(self.top_lid)
        self.scene.addItem(self.bottom_lid)

        # Transform origins
        self.outer.setTransformOriginPoint(size/2, size/2)
        self.pupil.setTransformOriginPoint(self.pupil_size/2, self.pupil_size/2)
        self.iris.setTransformOriginPoint(self.iris_size/2, self.iris_size/2)
        
        self.set_color(QColor(0, 200, 255)) # Default Cyan

        # Pulse animation for Iris
        self.iris_timer = QTimer()
        self.iris_timer.timeout.connect(self._do_iris_pulse)
        self.pulse_phase = 0.0
        self.iris_timer.start(200)

    def _do_iris_pulse(self):
        import math
        self.pulse_phase += 0.2
        op = 0.3 + 0.2 * math.sin(self.pulse_phase)
        self.iris.setOpacity(op)
        # Subtle scale pulse
        s = 1.0 + 0.05 * math.sin(self.pulse_phase)
        self.iris.setScale(s)

    def set_pulse_speed(self, interval):
        self.iris_timer.setInterval(interval)

    def set_pupil_position(self, dx, dy, animate=True):
        # dx, dy should be between -1 and 1
        limit = (self.base_size - self.pupil_size) / 2
        target_x = self.x + (self.base_size - self.pupil_size)/2 + dx * limit * 0.8
        target_y = self.y + (self.base_size - self.pupil_size)/2 + dy * limit * 0.8
        
        # Iris tracks pupil with a slight lag/offset for parallax
        iris_limit = (self.base_size - self.iris_size) / 2
        itarget_x = self.x + (self.base_size - self.iris_size)/2 + dx * iris_limit * 0.9
        itarget_y = self.y + (self.base_size - self.iris_size)/2 + dy * iris_limit * 0.9

        if animate:
            self.anim_p = QPropertyAnimation(self.pupil, b"pos")
            self.anim_p.setDuration(300)
            self.anim_p.setEndValue(QPointF(target_x, target_y))
            self.anim_p.setEasingCurve(QEasingCurve.OutQuad)
            self.anim_p.start()
            
            self.anim_i = QPropertyAnimation(self.iris, b"pos")
            self.anim_i.setDuration(400)
            self.anim_i.setEndValue(QPointF(itarget_x, itarget_y))
            self.anim_i.setEasingCurve(QEasingCurve.OutQuad)
            self.anim_i.start()
        else:
            self.pupil.setPos(target_x, target_y)
            self.iris.setPos(itarget_x, itarget_y)

    def set_color(self, color):
        # Outer glow - Premium Radial Gradient
        gradient = QRadialGradient(self.base_size/2, self.base_size/2, self.base_size/2)
        gradient.setColorAt(0, color)
        gradient.setColorAt(0.6, color.darker(130))
        gradient.setColorAt(1, Qt.transparent)
        self.outer.setBrush(QBrush(gradient))
        
        # Pupil style - Glowing Pupil
        pupil_gradient = QRadialGradient(self.pupil_size/2, self.pupil_size/2, self.pupil_size/2)
        pupil_gradient.setColorAt(0, Qt.black)
        pupil_gradient.setColorAt(0.8, color.darker(250))
        pupil_gradient.setColorAt(1, color.darker(150))
        self.pupil.setBrush(QBrush(pupil_gradient))
        self.pupil.setPen(QPen(color, 2))

        # Iris Glimmer Effect
        iris_gradient = QRadialGradient(self.iris_size/2, self.iris_size/2, self.iris_size/2)
        iris_gradient.setColorAt(0, color.lighter(150))
        iris_gradient.setColorAt(1, Qt.transparent)
        self.iris.setBrush(QBrush(iris_gradient))

    def set_lids(self, top_p, bottom_p, animate=True):
        # Control eyelids (0.0 to 1.0)
        if animate:
            self.ta = QPropertyAnimation(self.top_lid, b"progress")
            self.ba = QPropertyAnimation(self.bottom_lid, b"progress")
            for a, p in [(self.ta, top_p), (self.ba, bottom_p)]:
                a.setDuration(400)
                a.setEndValue(p)
                a.setEasingCurve(QEasingCurve.InOutQuad)
                a.start()
        else:
            self.top_lid.progress = top_p
            self.bottom_lid.progress = bottom_p

    def set_rotation(self, angle):
        # Animate eye tilt (Angry/Suspicious)
        anim = QPropertyAnimation(self.outer, b"rotation")
        anim.setDuration(400)
        anim.setEndValue(angle)
        anim.setEasingCurve(QEasingCurve.OutBack)
        anim.start()
        self._rot_anim = anim

    def blink(self):
        # Scale animation for blinking (OLED style)
        anim = QPropertyAnimation(self, b"eye_scale")
        anim.setDuration(150)
        anim.setStartValue(1.0)
        anim.setKeyValueAt(0.5, 0.01)
        anim.setEndValue(1.0) 
        anim.setEasingCurve(QEasingCurve.InOutQuad)
        anim.start()
        self._current_anim = anim

    def wink(self):
        # Quick dual-phase wink
        anim = QPropertyAnimation(self, b"eye_scale")
        anim.setDuration(400)
        anim.setStartValue(1.0)
        anim.setKeyValueAt(0.2, 0.01)
        anim.setKeyValueAt(0.4, 0.01)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.OutBounce)
        anim.start()
        self._current_anim = anim

    @pyqtProperty(float)
    def eye_scale(self):
        return self.outer.scale()
    
    @eye_scale.setter
    def eye_scale(self, value):
        self.outer.setScale(value)
        self.pupil.setScale(value)
        self.iris.setScale(value)

class AnimatedRectItem(QGraphicsObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rect_data = QRectF()
        self.brush_data = QBrush(Qt.black)
        self.pen_data = QPen(Qt.NoPen)

    def setRect(self, x, y, w, h):
        self.rect_data = QRectF(x, y, w, h)
        self.update()

    def setBrush(self, brush):
        self.brush_data = brush
        self.update()

    def setPen(self, pen):
        self.pen_data = pen
        self.update()

    def boundingRect(self):
        return self.rect_data

    def paint(self, painter, option, widget):
        painter.setBrush(self.brush_data)
        painter.setPen(self.pen_data)
        painter.drawRect(self.rect_data)

class StatusPanel(QObject):
    def __init__(self, scene, width, height):
        super().__init__()
        from PyQt5.QtWidgets import QGraphicsTextItem
        from PyQt5.QtGui import QFont, QTextOption

        self.scene = scene
        self.panel_w = min(430, max(300, int(width * 0.28)))
        self.panel_h = min(125, max(96, int(height * 0.14)))
        self.accent = QColor(0, 200, 255)

        self.bg_rect = AnimatedRectItem()
        self.bg_rect.setRect(0, 0, self.panel_w, self.panel_h)
        self.bg_rect.setBrush(QBrush(QColor(0, 0, 0, 210)))
        self.bg_rect.setPen(QPen(self.accent, 2))
        self.bg_rect.setPos(54, 38)
        self.bg_rect.setZValue(20)
        self.scene.addItem(self.bg_rect)

        self.text_item = QGraphicsTextItem(self.bg_rect)
        self.text_item.setDefaultTextColor(QColor(245, 250, 255))
        self.text_item.setFont(QFont("Ubuntu", 12, QFont.Bold))
        self.text_item.setTextWidth(self.panel_w - 28)
        self.text_item.setPos(14, 10)
        self.text_item.setZValue(21)

        option = QTextOption()
        option.setAlignment(Qt.AlignLeft)
        self.text_item.document().setDefaultTextOption(option)

        self.update_status({
            "mode": "ready",
            "phase": "listening",
            "message": "Ready. You can speak now.",
            "can_talk": True,
        })

    def set_accent_color(self, color):
        self.accent = QColor(color)
        self.bg_rect.setPen(QPen(self.accent, 2))

    def _objects_text(self, objects):
        if not objects:
            return "Camera: no target detected"
        parts = []
        for obj in objects[:2]:
            name = obj.get("name", "?")
            conf = obj.get("confidence", 0)
            pos = obj.get("position", "?")
            try:
                conf_txt = f"{float(conf):.0%}"
            except Exception:
                conf_txt = str(conf)
            parts.append(f"{name} {conf_txt} {pos}")
        return "Cam: " + ", ".join(parts)

    def update_status(self, status):
        if not isinstance(status, dict):
            return

        mode = str(status.get("mode", "ready")).replace("_", " ").upper()
        target = str(status.get("target", "")).strip()
        phase = str(status.get("phase", "")).replace("_", " ").strip()
        message = str(status.get("message", "")).strip()
        can_talk = bool(status.get("can_talk", False))
        searched = int(status.get("searched_count", 0) or 0)
        wp_idx = int(status.get("waypoint_index", 0) or 0)
        wp_total = int(status.get("waypoint_total", 0) or 0)
        objects = status.get("objects", []) or []

        if target:
            title = f"{mode}: {target}"
        else:
            title = mode

        progress = ""
        if wp_total > 0:
            shown_idx = wp_idx if wp_idx > 0 else searched
            progress = f"Pt {shown_idx}/{wp_total}  Searched {searched}"
        elif searched:
            progress = f"Searched: {searched} places"

        listen_line = "Talk now" if can_talk else "Busy"

        lines = [title]
        if phase:
            lines.append(f"State: {phase}")
        if progress:
            lines.append(progress)
        lines.append(self._objects_text(objects))
        if message:
            lines.append(message[:58])
        lines.append(listen_line)

        self.text_item.setPlainText("\n".join(lines[:5]))
        self.scene.update()


class ChatOverlay(QObject):
    finished_signal = pyqtSignal()

    def __init__(self, scene, width, height):
        super().__init__()

        self.scene = scene
        self.margin = 100
        self.screen_width = width
        self.screen_height = height

        from PyQt5.QtWidgets import QGraphicsTextItem
        from PyQt5.QtGui import QFont, QTextOption

        # Background rectangle at bottom of screen
        self.bg_rect = AnimatedRectItem()
        self.bg_rect.setBrush(QBrush(QColor(0, 0, 0)))
        self.bg_rect.setPen(QPen(Qt.NoPen))

        # Slightly taller text box
        self.bg_rect.setRect(0, 0, self.screen_width, 220)
        self.bg_rect.setPos(0, self.screen_height - 240)
        self.bg_rect.setZValue(40)
        self.scene.addItem(self.bg_rect)

        # Text item
        self.text_item = QGraphicsTextItem(self.bg_rect)
        self.text_item.setDefaultTextColor(QColor(255, 255, 255))
        self.text_item.setZValue(41)

        option = QTextOption()
        option.setAlignment(Qt.AlignCenter)
        self.text_item.document().setDefaultTextOption(option)

        # Smaller font to fit more safely
        font = QFont("Ubuntu", 26, QFont.Bold)
        self.text_item.setFont(font)

        self.text_item.setTextWidth(width - 4 * self.margin)
        self.text_item.setPos(self.margin * 2, 30)

        self.full_text = ""
        self.words = []
        self.current_word_idx = 0

        # Queue system
        self.message_queue = []
        self.is_showing_message = False

        # Maximum words per screen page
        self.max_words_per_chunk = 30

        # Typing timer
        self.timer = QTimer()
        self.timer.timeout.connect(self._type_step)

        # Hide timer
        self.hide_timer = QTimer()
        self.hide_timer.setSingleShot(True)
        self.hide_timer.timeout.connect(self.hide)

    def set_accent_color(self, color):
        pass

    def split_text_into_chunks(self, text):
        """
        Split long text into chunks of maximum 30 words.
        Each chunk will be displayed separately.
        """
        words = text.strip().split()

        if len(words) <= self.max_words_per_chunk:
            return [text.strip()]

        chunks = []

        for i in range(0, len(words), self.max_words_per_chunk):
            chunk_words = words[i:i + self.max_words_per_chunk]
            chunk = " ".join(chunk_words)
            chunks.append(chunk)

        return chunks

    def show_message(self, text):
        """
        Receive one message, split it into 30-word chunks,
        then add each chunk to the queue.
        """
        print("DEBUG: received message:", text)

        if not text:
            return

        chunks = self.split_text_into_chunks(text)

        for chunk in chunks:
            self.message_queue.append(chunk)

        if not self.is_showing_message:
            self._show_next_message()

    def _show_next_message(self):
        """
        Display the next queued chunk.
        """
        if not self.message_queue:
            self.is_showing_message = False
            return

        self.is_showing_message = True

        text = self.message_queue.pop(0)
        print("DEBUG: showing chunk:", text)

        self.timer.stop()
        self.hide_timer.stop()

        if hasattr(self, "anim"):
            self.anim.stop()

        self.full_text = text
        self.words = text.split()
        self.current_word_idx = 0

        self.text_item.setPlainText("")
        self.bg_rect.setOpacity(1.0)

        # Word-by-word typing speed
        self.timer.start(110)
        self.scene.update()

    def _type_step(self):
        """
        Type the current chunk word by word.
        """
        if self.current_word_idx < len(self.words):
            current_text = " ".join(self.words[:self.current_word_idx + 1])
            self.text_item.setPlainText(current_text)
            self.current_word_idx += 1
            self.scene.update()
        else:
            self.timer.stop()

            # Time to read current chunk
            display_time = max(2200, len(self.words) * 180)
            self.hide_timer.start(display_time)

    def hide(self):
        """
        Hide current chunk, then show the next chunk.
        """
        self.anim = QPropertyAnimation(self.bg_rect, b"opacity")
        self.anim.setDuration(600)
        self.anim.setStartValue(1.0)
        self.anim.setEndValue(0.0)

        def after_hide():
            self.finished_signal.emit()

            # Small pause before next chunk
            QTimer.singleShot(250, self._show_next_message)

        self.anim.finished.connect(after_hide)
        self.anim.start()

class RobotFrame(QObject):
    def __init__(self, scene, width, height):
        super().__init__()
        self.scene = scene
        self.w = width
        self.h = height
        
        # Base Frame Style: Dark, Metallic
        brush = QBrush(QColor(20, 20, 25))
        pen = QPen(QColor(60, 60, 70), 3)
        
        # Main Border
        border_thickness = 40
        self.frame = QGraphicsPathItem()
        path = QPainterPath()
        path.setFillRule(Qt.OddEvenFill)
        # Outer screen rect
        path.addRect(0, 0, self.w, self.h)
        # Inner "face" rect (hollowed out)
        path.addRect(border_thickness, border_thickness, self.w - 2*border_thickness, self.h - 2*border_thickness)
        self.frame.setPath(path)
        self.frame.setBrush(brush)
        self.frame.setPen(pen)
        self.scene.addItem(self.frame)

        # Corner Accents (Futuristic Brackets)
        self.corners = []
        corner_size = 150
        accent_pen = QPen(QColor(0, 200, 255, 150), 4)
        
        # Function to create bracket path
        def make_bracket(x, y, flip_x=False, flip_y=False):
            p = QPainterPath()
            sx = 1 if not flip_x else -1
            sy = 1 if not flip_y else -1
            p.moveTo(x + sx*corner_size, y)
            p.lineTo(x, y)
            p.lineTo(x, y + sy*corner_size)
            item = QGraphicsPathItem(p)
            item.setPen(accent_pen)
            self.scene.addItem(item)
            return item

        self.corners.append(make_bracket(10, 10)) # Top Left
        self.corners.append(make_bracket(self.w-10, 10, flip_x=True)) # Top Right
        self.corners.append(make_bracket(10, self.h-10, flip_y=True)) # Bottom Left
        self.corners.append(make_bracket(self.w-10, self.h-10, flip_x=True, flip_y=True)) # Bottom Right

        # Breathing timer
        self.breath_timer = QTimer()
        self.breath_timer.timeout.connect(self._do_breath)
        self.breath_phase = 0.0
        self.breath_timer.start(50)
        self.base_color = QColor(0, 200, 255)

    def _do_breath(self):
        import math
        self.breath_phase += 0.05
        opacity = 120 + 80 * math.sin(self.breath_phase)
        color = QColor(self.base_color)
        color.setAlpha(int(opacity))
        pen = QPen(color, 4 + 2 * math.sin(self.breath_phase))
        for c in self.corners:
            c.setPen(pen)

    def set_accent_color(self, color):
        self.base_color = color

class RobotFace(QMainWindow):
    def __init__(self):
        super().__init__()
        # Fullscreen robot face window. It is not always-on-top so RViz can appear above it.
        self.setWindowFlags(Qt.FramelessWindowHint)

        # Do NOT use transparent background
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)

        # Solid black full-screen background
        self.setStyleSheet("background-color: black;")

        self.scene = QGraphicsScene()
        
        self.view = QGraphicsView(self.scene)
        self.view.setRenderHint(QPainter.Antialiasing)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.view.setFrameStyle(QFrame.NoFrame)

        # Solid black scene/view background
        self.view.setStyleSheet("background-color: black; border: none;")
        self.scene.setBackgroundBrush(QBrush(QColor(0, 0, 0)))

        self.setCentralWidget(self.view)
        
        screen_geo = QApplication.primaryScreen().geometry()
        self.width = screen_geo.width()
        self.height = screen_geo.height()
        self.setGeometry(screen_geo)
        self.scene.setSceneRect(0, 0, self.width, self.height)
        
        eye_spacing = self.width * 0.22
        eye_y = self.height * 0.25
        self.eye_size = self.width * 0.18
        self.left_eye = Eye(self.scene, (self.width/2) - eye_spacing - (self.eye_size/2), eye_y, self.eye_size)
        self.right_eye = Eye(self.scene, (self.width/2) + eye_spacing - (self.eye_size/2), eye_y, self.eye_size)
        
        # Mouth
        mouth_w = self.width * 0.15
        mouth_h = 100
        self.mouth = Mouth((self.width - mouth_w)/2, self.height * 0.6, mouth_w, mouth_h)
        self.scene.addItem(self.mouth)
        
        # Professional Frame (Cadre)
        self.cadre = RobotFrame(self.scene, self.width, self.height)
        self.status_panel = StatusPanel(self.scene, self.width, self.height)
        
        self.chat = ChatOverlay(self.scene, self.width, self.height)
        self.chat.finished_signal.connect(lambda: self.set_emotion("neutral"))
        

        
        self.emotion = "neutral"
        
        self.blink_timer = QTimer()
        self.blink_timer.timeout.connect(self.random_blink)
        self.blink_timer.start(3000)
        self.look_timer = QTimer()
        self.look_timer.timeout.connect(self.idle_look)
        self.look_timer.start(4000)
        
        self.comm = CommunicationHandler()
        self.comm.emotion_signal.connect(self.set_emotion)
        self.comm.message_signal.connect(self.chat.show_message)
        self.comm.vel_signal.connect(self.vel_callback)
        self.comm.status_signal.connect(self.status_callback)
        self.comm.scan_signal.connect(self.scan_callback)
        self.comm.search_status_signal.connect(self.status_panel.update_status)
        self.comm.search_status_signal.connect(self.face_status_callback)
        self.comm.start_listeners()

        self._build_control_buttons()

    def _build_control_buttons(self):
        button_w = min(118, max(92, int(self.width * 0.095)))
        button_h = 42
        gap = 10
        x = self.width - (button_w * 3) - (gap * 2) - 54
        y = 44

        self.stop_search_button = QPushButton("Stop", self)
        self.rviz_button = QPushButton("RViz", self)
        default_muted = os.environ.get("AI_ROBOT_MIC_DEFAULT_MUTED", "1").strip() != "0"
        self.mic_button = QPushButton("Mic OFF" if default_muted else "Mic ON", self)
        self.mic_muted = default_muted

        for btn in (self.stop_search_button, self.rviz_button, self.mic_button):
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFocusPolicy(Qt.NoFocus)
            btn.setStyleSheet("""
                QPushButton {
                    background: rgba(0, 0, 0, 205);
                    color: rgb(245, 250, 255);
                    border: 2px solid rgb(0, 200, 255);
                    border-radius: 6px;
                    font: bold 14px Ubuntu;
                    padding: 4px 10px;
                }
                QPushButton:hover {
                    background: rgba(0, 65, 90, 225);
                }
                QPushButton:pressed {
                    background: rgba(0, 120, 160, 235);
                }
            """)

        self.stop_search_button.setGeometry(x, y, button_w, button_h)
        self.rviz_button.setGeometry(x + button_w + gap, y, button_w, button_h)
        self.mic_button.setGeometry(x + (button_w * 2) + (gap * 2), y, button_w, button_h)
        
        self.stop_search_button.clicked.connect(lambda: self._send_ui_command("stop_search"))
        self.rviz_button.clicked.connect(self._open_rviz_from_button)
        self.mic_button.clicked.connect(self._toggle_mic)
        
        self.stop_search_button.show()
        self.rviz_button.show()
        self.mic_button.show()

    def _toggle_mic(self):
        self.mic_muted = not self.mic_muted
        if self.mic_muted:
            self.mic_button.setText("Mic OFF")
            self._send_ui_command("mic_off")
        else:
            self.mic_button.setText("Mic ON")
            self._send_ui_command("mic_on")

    def _open_rviz_from_button(self):
        self._send_ui_command("show_rviz")
        self.lower()

    def _send_ui_command(self, command):
        import socket
        payload = json.dumps({"type": command}).encode("utf-8")
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.sendto(payload, ("127.0.0.1", 5006))
            sock.close()
        except Exception as e:
            print(f"UI command send error: {e}")

    def random_blink(self):
        self.left_eye.blink()
        self.right_eye.blink()
        self.blink_timer.start(random.randint(2000, 7000))

    def idle_look(self):
        if self.emotion == "neutral":
            if random.random() > 0.3:
                dx = random.choice([-0.7, 0.7, 0.1, -0.1])
                dy = random.uniform(-0.1, 0.1)
                self.left_eye.set_pupil_position(dx, dy)
                self.right_eye.set_pupil_position(dx, dy)
                QTimer.singleShot(random.randint(1000, 3000), self.reset_eyes)
        self.look_timer.start(random.randint(3000, 6000))

    def reset_eyes(self):
        if self.emotion == "neutral":
            self.left_eye.set_pupil_position(0, 0)
            self.right_eye.set_pupil_position(0, 0)

    def set_emotion(self, emotion):
        if emotion == self.emotion:
            self._apply_emotion_pose(emotion)
            return
        
        # Override current emotion
        self.emotion = emotion
        self._apply_emotion_pose(emotion)
        
        # Reset to neutral after 10s if no chat message is active
        # Otherwise, the ChatOverlay.finished_signal will handle it
        if emotion != "neutral":
            self.reset_timer = QTimer()
            self.reset_timer.singleShot(10000, self.check_reset_to_neutral)

    def check_reset_to_neutral(self):
        # Only reset if chat is not currently showing a message
        if self.chat.text_item.opacity() < 0.1:
            self.set_emotion("neutral")

    def _apply_emotion_pose(self, emotion):
        colors = {
            "neutral": QColor(0, 200, 255), "happy": QColor(0, 255, 120), 
            "thinking": QColor(255, 220, 0), "sad": QColor(255, 30, 30), 
            "fear": QColor(255, 120, 0), "angry": QColor(255, 0, 80)
        }
        color = colors.get(emotion, QColor(180, 180, 180))
        self.left_eye.set_color(color)
        self.right_eye.set_color(color)
        self.mouth.set_color(color)
        
        # Update Frame Accent
        self.cadre.set_accent_color(color)
        
        # Update Chat Bubble Accent to match the emotional theme color
        if hasattr(self, 'chat'):
            self.chat.set_accent_color(color)
        if hasattr(self, 'status_panel'):
            self.status_panel.set_accent_color(color)

        # Variations: [ (scale, px, py, rotL, rotR, lidT, lidB, mouthCurve), ... ]
        variations = {
            "neutral": [(1.0, 0, 0, 0, 0, 0.0, 0.0, 0.0)],
            "sleep": [(1.0, 0, 0, 0, 0, 1.0, 1.0, 0.0)],
            "happy": [
                (1.0, 0, -0.4, 0, 0, 0.1, 0.0, 1.0),    
                (1.0, 0.4, -0.3, 15, -15, 0.2, 0.1, 1.2),
                (1.0, -0.2, -0.4, 0, 0, 0.3, 0.0, 0.8)
            ],
            "thinking": [
                (1.0, 0.5, -0.5, 5, 5, 0.3, 0.0, -0.2),  
                (1.0, -0.4, -0.6, -10, -10, 0.4, 0.0, 0.2),
                (1.0, 0, -0.7, 0, 0, 0.2, 0.1, 0.0)
            ],
            "sad": [
                (1.0, 0, 0.6, 10, -10, 0.6, 0.0, -1.0),     
                (1.0, 0.2, 0.5, -20, 20, 0.5, 0.1, -1.2),
                (1.0, -0.2, 0.5, -15, 15, 0.5, 0.0, -0.8)
            ],
            "fear": [
                (1.0, 0, 0, 0, 0, 0.0, 0.0, 0.3),       
                (1.0, -0.4, 0.4, 20, -20, 0.0, 0.0, 0.5),
                (1.0, 0.3, 0.3, -10, 10, 0.0, 0.0, 0.4)
            ],
            "angry": [
                (1.0, 0, 0.3, 30, -30, 0.4, 0.3, -0.5),  
                (1.0, 0.4, 0.2, 35, -20, 0.5, 0.2, -0.4),
                (1.0, -0.4, 0.2, 20, -35, 0.4, 0.4, -0.6)
            ]
        }

        v = random.choice(variations.get(emotion, variations["neutral"]))
        scale, px, py, rotL, rotR, lidT, lidB, mCurve = v
        self.animate_eyes(scale, px, py)
        self.left_eye.set_rotation(rotL)
        self.right_eye.set_rotation(rotR)
        self.left_eye.set_lids(lidT, lidB)
        self.right_eye.set_lids(lidT, lidB)
        
        # Animate mouth curve
        m_anim = QPropertyAnimation(self.mouth, b"curve")
        m_anim.setDuration(500)
        m_anim.setEndValue(mCurve)
        m_anim.setEasingCurve(QEasingCurve.OutBack)
        m_anim.start()
        self._m_anim = m_anim

        # Intelligence: Specialized behaviors
        if emotion == "happy":
            if random.random() > 0.4: QTimer.singleShot(700, self.right_eye.wink)
            if random.random() > 0.7: QTimer.singleShot(1200, self.left_eye.wink)
        elif emotion == "fear":
            self.do_shake()
        elif emotion == "angry":
            # Angry growl animation (throb)
            for i in range(3):
                QTimer.singleShot(300*i, lambda: self.animate_eyes(0.9, px, py+0.05))
                QTimer.singleShot(300*i + 150, lambda: self.animate_eyes(1.0, px, py))
        elif emotion == "sad":
            # Slow blink/sigh
            QTimer.singleShot(1000, self.left_eye.blink)
            QTimer.singleShot(1100, self.right_eye.blink)
        
        # Advanced: Iris Pulse logic based on emotion "Energy"
        energy_map = {"fear": 50, "angry": 60, "happy": 100, "neutral": 200, "thinking": 300, "sad": 500}
        interval = energy_map.get(emotion, 200)
        self.left_eye.set_pulse_speed(interval)
        self.right_eye.set_pulse_speed(interval)

    def animate_eyes(self, scale, px, py):
        # Animate both eyes to a specific scale and pupil position
        self.l_scale_anim = QPropertyAnimation(self.left_eye, b"eye_scale")
        self.r_scale_anim = QPropertyAnimation(self.right_eye, b"eye_scale")
        for a in [self.l_scale_anim, self.r_scale_anim]:
            a.setDuration(500)
            a.setEndValue(scale)
            a.setEasingCurve(QEasingCurve.OutBack)
            a.start()
        
        self.left_eye.set_pupil_position(px, py)
        self.right_eye.set_pupil_position(px, py)

    def do_shake(self):
        # Subtle fear shake
        if self.emotion == "fear":
            offset_x = random.uniform(-10, 10)
            offset_y = random.uniform(-10, 10)
            self.view.resetTransform()
            self.view.translate(offset_x, offset_y)
            QTimer.singleShot(50, self.do_shake)
        else:
            self.view.resetTransform()

    def face_status_callback(self, status):
        if not isinstance(status, dict):
            return
        mode = str(status.get("mode", "")).lower()
        phase = str(status.get("phase", "")).lower()
        if mode == "found" or phase == "found" or status.get("found"):
            self.set_emotion("happy")
        elif mode == "not_found" or phase in ["not_found", "rejected", "navigation_failed"]:
            self.set_emotion("sad")
        elif mode in ["ready", "stopped"] or phase in ["cancelled", "stopped"]:
            self.set_emotion("neutral")
        elif phase == "recovery":
            self.set_emotion("fear")
        elif mode in ["searching", "thinking"]:
            self.set_emotion("thinking")

    def status_callback(self, status):
        if status == 1: self.set_emotion("thinking")
        elif status == 3: self.set_emotion("happy")
        elif status in [4, 5, 9]: self.set_emotion("sad")

    def vel_callback(self, linear, angular):
        dx = max(-1.0, min(1.0, -angular * 1.5))
        dy = max(-1.0, min(1.0, -linear * 0.5))
        self.left_eye.set_pupil_position(dx, dy)
        self.right_eye.set_pupil_position(dx, dy)

    def scan_callback(self, dist):
        # Advanced: Reflexive response to obstacles
        if dist < 0.4:
            if self.emotion != "fear" and self.emotion != "angry":
                self.set_emotion("fear")
        elif dist < 1.0:
            if self.emotion == "neutral":
                # Look suspicious/thinking if something is in front but not too close
                self.set_emotion("thinking")
        elif dist > 2.0 and self.emotion in ["fear", "thinking"]:
            # Obstacle cleared
            self.set_emotion("neutral")

if __name__ == "__main__":
    import signal
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    app = QApplication(sys.argv)
    face = RobotFace()
    face.showFullScreen()
    sys.exit(app.exec_())
