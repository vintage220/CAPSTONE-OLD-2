# Imports
import comtypes
import cv2
import mediapipe as mp
import pyautogui
import math
from enum import IntEnum
from ctypes import cast, POINTER
from comtypes import CLSCTX_ALL
from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
from google.protobuf.json_format import MessageToDict
import screen_brightness_control as sbcontrol

# Fix for pyautogui failsafe
pyautogui.FAILSAFE = False
mp_drawing = mp.solutions.drawing_utils
mp_hands = mp.solutions.hands

# Gesture Encodings 
class Gest(IntEnum):
    """
    Enum for mapping all hand gesture to binary number.
    """
    FIST = 0
    PINKY = 1
    RING = 2
    MID = 4
    LAST3 = 7
    INDEX = 8
    FIRST2 = 12
    LAST4 = 15
    THUMB = 16    
    PALM = 31
    
    # Extra Mappings
    V_GEST = 33
    TWO_FINGER_CLOSED = 34
    PINCH_MAJOR = 35
    PINCH_MINOR = 36

# Multi-handedness Labels
class HLabel(IntEnum):
    MINOR = 0
    MAJOR = 1

# Convert Mediapipe Landmarks to recognizable Gestures
class HandRecog:
    """
    Convert Mediapipe Landmarks to recognizable Gestures.
    """
    
    def __init__(self, hand_label):
        self.finger = 0
        self.ori_gesture = Gest.PALM
        self.prev_gesture = Gest.PALM
        self.frame_count = 0
        self.hand_result = None
        self.hand_label = hand_label
    
    def update_hand_result(self, hand_result):
        self.hand_result = hand_result

    def get_signed_dist(self, point):
        """returns signed euclidean distance between 'point'."""
        sign = -1
        if self.hand_result.landmark[point[0]].y < self.hand_result.landmark[point[1]].y:
            sign = 1
        dist = (self.hand_result.landmark[point[0]].x - self.hand_result.landmark[point[1]].x)**2
        dist += (self.hand_result.landmark[point[0]].y - self.hand_result.landmark[point[1]].y)**2
        dist = math.sqrt(dist)
        return dist*sign
    
    def get_dist(self, point):
        """returns euclidean distance between 'point'."""
        dist = (self.hand_result.landmark[point[0]].x - self.hand_result.landmark[point[1]].x)**2
        dist += (self.hand_result.landmark[point[0]].y - self.hand_result.landmark[point[1]].y)**2
        dist = math.sqrt(dist)
        return dist
    
    def get_dz(self,point):
        """returns absolute difference on z-axis between 'point'."""
        return abs(self.hand_result.landmark[point[0]].z - self.hand_result.landmark[point[1]].z)
    
    def set_finger_state(self):
        """set 'finger' by computing ratio of distance between finger tip, middle knuckle, base knuckle."""
        if self.hand_result == None:
            return

        points = [[8,5,0],[12,9,0],[16,13,0],[20,17,0]]
        self.finger = 0
        self.finger = self.finger | 0 #thumb
        for idx,point in enumerate(points):
            
            dist = self.get_signed_dist(point[:2])
            dist2 = self.get_signed_dist(point[1:])
            
            try:
                ratio = round(dist/dist2,1)
            except:
                ratio = round(dist/0.01,1)  # Fixed: was dist1/0.01

            self.finger = self.finger << 1
            if ratio > 0.5 :
                self.finger = self.finger | 1

    def get_gesture(self):
        """returns int representing gesture corresponding to Enum 'Gest'."""
        if self.hand_result == None:
            return Gest.PALM

        current_gesture = Gest.PALM
        if self.finger in [Gest.LAST3,Gest.LAST4] and self.get_dist([8,4]) < 0.05:
            if self.hand_label == HLabel.MINOR :
                current_gesture = Gest.PINCH_MINOR
            else:
                current_gesture = Gest.PINCH_MAJOR

        elif Gest.FIRST2 == self.finger :
            point = [[8,12],[5,9]]
            dist1 = self.get_dist(point[0])
            dist2 = self.get_dist(point[1])
            ratio = dist1/dist2
            if ratio > 1.7:
                current_gesture = Gest.V_GEST
            else:
                if self.get_dz([8,12]) < 0.1:
                    current_gesture =  Gest.TWO_FINGER_CLOSED
                else:
                    current_gesture =  Gest.MID
        else:
            current_gesture =  self.finger
        
        if current_gesture == self.prev_gesture:
            self.frame_count += 1
        else:
            self.frame_count = 0

        self.prev_gesture = current_gesture

        if self.frame_count > 4 :
            self.ori_gesture = current_gesture
        return self.ori_gesture

# Executes commands according to detected gestures
class Controller:
    """Executes commands according to detected gestures."""

    tx_old = 0
    ty_old = 0
    trial = True
    flag = False
    grabflag = False
    pinchmajorflag = False
    pinchminorflag = False
    pinchstartxcoord = None
    pinchstartycoord = None
    pinchdirectionflag = None
    prevpinchlv = 0
    pinchlv = 0
    framecount = 0
    prev_hand = None
    pinch_threshold = 0.3
    
    @staticmethod
    def getpinchylv(hand_result):
        """returns distance beween starting pinch y coord and current hand position y coord."""
        dist = round((Controller.pinchstartycoord - hand_result.landmark[8].y)*10,1)
        return dist

    @staticmethod
    def getpinchxlv(hand_result):
        """returns distance beween starting pinch x coord and current hand position x coord."""
        dist = round((hand_result.landmark[8].x - Controller.pinchstartxcoord)*10,1)
        return dist
    
    @staticmethod
    def changesystembrightness():
        """sets system brightness based on 'Controller.pinchlv'."""
        try:
            currentBrightnessLv = sbcontrol.get_brightness()/100.0
            currentBrightnessLv += Controller.pinchlv/50.0
            if currentBrightnessLv > 1.0:
                currentBrightnessLv = 1.0
            elif currentBrightnessLv < 0.0:
                currentBrightnessLv = 0.0       
            sbcontrol.fade_brightness(int(100*currentBrightnessLv) , start = sbcontrol.get_brightness())
        except Exception as e:
            print(f"Brightness control error: {e}")
    
    @staticmethod
    def changesystemvolume():
        """sets system volume based on 'Controller.pinchlv'."""
        try:
            devices = AudioUtilities.GetSpeakers()
            interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            volume = cast(interface, POINTER(IAudioEndpointVolume))
            currentVolumeLv = volume.GetMasterVolumeLevelScalar()
            currentVolumeLv += Controller.pinchlv/50.0
            if currentVolumeLv > 1.0:
                currentVolumeLv = 1.0
            elif currentVolumeLv < 0.0:
                currentVolumeLv = 0.0
            volume.SetMasterVolumeLevelScalar(currentVolumeLv, None)
        except Exception as e:
            print(f"Volume control error: {e}")
    
    @staticmethod
    def scrollVertical():
        """scrolls on screen vertically."""
        try:
            pyautogui.scroll(120 if Controller.pinchlv>0.0 else -120)
        except Exception as e:
            print(f"Vertical scroll error: {e}")
    
    @staticmethod
    def scrollHorizontal():
        """scrolls on screen horizontally."""
        try:
            pyautogui.keyDown('shift')
            pyautogui.keyDown('ctrl')
            pyautogui.scroll(-120 if Controller.pinchlv>0.0 else 120)
            pyautogui.keyUp('ctrl')
            pyautogui.keyUp('shift')
        except Exception as e:
            print(f"Horizontal scroll error: {e}")

    @staticmethod
    def get_position(hand_result):
        """returns coordinates of current hand position."""
        point = 9
        position = [hand_result.landmark[point].x ,hand_result.landmark[point].y]
        sx,sy = pyautogui.size()
        x_old,y_old = pyautogui.position()
        x = int(position[0]*sx)
        y = int(position[1]*sy)
        if Controller.prev_hand is None:
            Controller.prev_hand = x,y
        delta_x = x - Controller.prev_hand[0]
        delta_y = y - Controller.prev_hand[1]

        distsq = delta_x**2 + delta_y**2
        ratio = 1
        Controller.prev_hand = [x,y]

        if distsq <= 25:
            ratio = 0
        elif distsq <= 900:
            ratio = 0.07 * (distsq ** (1/2))
        else:
            ratio = 2.1
        x , y = x_old + delta_x*ratio , y_old + delta_y*ratio
        return (x,y)

    @staticmethod
    def pinch_control_init(hand_result):
        """Initializes attributes for pinch gesture."""
        Controller.pinchstartxcoord = hand_result.landmark[8].x
        Controller.pinchstartycoord = hand_result.landmark[8].y
        Controller.pinchlv = 0
        Controller.prevpinchlv = 0
        Controller.framecount = 0

    @staticmethod
    def pinch_control(hand_result, controlHorizontal, controlVertical):
        """calls 'controlHorizontal' or 'controlVertical' based on pinch flags."""
        if Controller.framecount == 5:
            Controller.framecount = 0
            Controller.pinchlv = Controller.prevpinchlv

            if Controller.pinchdirectionflag == True:
                controlHorizontal() #x
            elif Controller.pinchdirectionflag == False:
                controlVertical() #y

        lvx =  Controller.getpinchxlv(hand_result)
        lvy =  Controller.getpinchylv(hand_result)
            
        if abs(lvy) > abs(lvx) and abs(lvy) > Controller.pinch_threshold:
            Controller.pinchdirectionflag = False
            if abs(Controller.prevpinchlv - lvy) < Controller.pinch_threshold:
                Controller.framecount += 1
            else:
                Controller.prevpinchlv = lvy
                Controller.framecount = 0

        elif abs(lvx) > Controller.pinch_threshold:
            Controller.pinchdirectionflag = True
            if abs(Controller.prevpinchlv - lvx) < Controller.pinch_threshold:
                Controller.framecount += 1
            else:
                Controller.prevpinchlv = lvx
                Controller.framecount = 0

    @staticmethod
    def handle_controls(gesture, hand_result):  
        """Implements all gesture functionality."""      
        x,y = None,None
        if gesture != Gest.PALM :
            x,y = Controller.get_position(hand_result)
        
        # flag reset
        if gesture != Gest.FIST and Controller.grabflag:
            Controller.grabflag = False
            pyautogui.mouseUp(button = "left")

        if gesture != Gest.PINCH_MAJOR and Controller.pinchmajorflag:
            Controller.pinchmajorflag = False

        if gesture != Gest.PINCH_MINOR and Controller.pinchminorflag:
            Controller.pinchminorflag = False

        # implementation
        if gesture == Gest.V_GEST:
            Controller.flag = True
            pyautogui.moveTo(x, y, duration = 0.1)

        elif gesture == Gest.FIST:
            if not Controller.grabflag : 
                Controller.grabflag = True
                pyautogui.mouseDown(button = "left")
            pyautogui.moveTo(x, y, duration = 0.1)

        elif gesture == Gest.MID and Controller.flag:
            pyautogui.click()
            Controller.flag = False

        elif gesture == Gest.INDEX and Controller.flag:
            pyautogui.click(button='right')
            Controller.flag = False

        elif gesture == Gest.TWO_FINGER_CLOSED and Controller.flag:
            pyautogui.doubleClick()
            Controller.flag = False

        elif gesture == Gest.PINCH_MINOR:
            if Controller.pinchminorflag == False:
                Controller.pinch_control_init(hand_result)
                Controller.pinchminorflag = True
            Controller.pinch_control(hand_result,Controller.scrollHorizontal, Controller.scrollVertical)
        
        elif gesture == Gest.PINCH_MAJOR:
            if Controller.pinchmajorflag == False:
                Controller.pinch_control_init(hand_result)
                Controller.pinchmajorflag = True
            Controller.pinch_control(hand_result,Controller.changesystembrightness, Controller.changesystemvolume)

class GestureController:
    """
    Handles camera, obtain landmarks from mediapipe, entry point for whole program.
    """
    gc_mode = 0
    cap = None
    CAM_HEIGHT = None
    CAM_WIDTH = None
    hr_major = None # Right Hand by default
    hr_minor = None # Left hand by default
    dom_hand = True

    def __init__(self):
        """Initializes attributes."""
        print("Initializing Virtual Mouse...")
        print("Jerry Welcomes you!")
        
        GestureController.gc_mode = 1
        
        # Enhanced camera initialization
        print("Accessing camera...")
        for camera_index in range(3):  # Try camera indices 0, 1, 2
            GestureController.cap = cv2.VideoCapture(camera_index)
            if GestureController.cap.isOpened():
                ret, frame = GestureController.cap.read()
                if ret:
                    print(f"Camera {camera_index} is working!")
                    break
                else:
                    GestureController.cap.release()
            else:
                GestureController.cap.release()
        
        if not GestureController.cap.isOpened():
            print("ERROR: Cannot access camera!")
            print("Make sure:")
            print("1. Camera is connected and working")
            print("2. No other applications are using the camera")
            print("3. Camera permissions are enabled")
            return
        
        # Set camera properties for better performance
        GestureController.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        GestureController.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        GestureController.cap.set(cv2.CAP_PROP_FPS, 30)
        
        GestureController.CAM_HEIGHT = GestureController.cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
        GestureController.CAM_WIDTH = GestureController.cap.get(cv2.CAP_PROP_FRAME_WIDTH)
        
        print(f"Camera initialized: {int(GestureController.CAM_WIDTH)}x{int(GestureController.CAM_HEIGHT)}")
    
    @staticmethod
    def classify_hands(results):
        """sets 'hr_major', 'hr_minor' based on classification of hand."""
        left , right = None,None
        try:
            handedness_dict = MessageToDict(results.multi_handedness[0])
            if handedness_dict['classification'][0]['label'] == 'Right':
                right = results.multi_hand_landmarks[0]
            else :
                left = results.multi_hand_landmarks[0]
        except:
            pass

        try:
            handedness_dict = MessageToDict(results.multi_handedness[1])
            if handedness_dict['classification'][0]['label'] == 'Right':
                right = results.multi_hand_landmarks[1]
            else :
                left = results.multi_hand_landmarks[1]
        except:
            pass
        
        if GestureController.dom_hand == True:
            GestureController.hr_major = right
            GestureController.hr_minor = left
        else :
            GestureController.hr_major = left
            GestureController.hr_minor = right

    def start(self):
        """Entry point of whole program."""
        
        if not GestureController.cap or not GestureController.cap.isOpened():
            print("Camera not available. Cannot start gesture controller.")
            return
        
        print("\nGesture Controls:")
        print("✋ V-Shape: Activate cursor mode")
        print("👆 Index (after V): Left click")
        print("🤟 Middle (after V): Right click")  
        print("✌️ Two fingers closed (after V): Double click")
        print("✊ Fist: Drag")
        print("🤏 Pinch (Minor hand): Scroll")
        print("🤏 Pinch (Major hand): Volume/Brightness")
        print("Press ENTER to quit\n")
        
        handmajor = HandRecog(HLabel.MAJOR)
        handminor = HandRecog(HLabel.MINOR)

        with mp_hands.Hands(max_num_hands = 2,min_detection_confidence=0.5, min_tracking_confidence=0.5) as hands:
            frame_count = 0
            while GestureController.cap.isOpened() and GestureController.gc_mode:
                success, image = GestureController.cap.read()

                if not success:
                    print("Warning: Empty camera frame.")
                    continue
                
                frame_count += 1
                
                # Flip horizontally for mirror effect
                image = cv2.cvtColor(cv2.flip(image, 1), cv2.COLOR_BGR2RGB)
                image.flags.writeable = False
                results = hands.process(image)
                
                image.flags.writeable = True
                image = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)

                if results.multi_hand_landmarks:                   
                    GestureController.classify_hands(results)
                    handmajor.update_hand_result(GestureController.hr_major)
                    handminor.update_hand_result(GestureController.hr_minor)

                    handmajor.set_finger_state()
                    handminor.set_finger_state()
                    gest_name = handminor.get_gesture()

                    if gest_name == Gest.PINCH_MINOR:
                        Controller.handle_controls(gest_name, handminor.hand_result)
                    else:
                        gest_name = handmajor.get_gesture()
                        Controller.handle_controls(gest_name, handmajor.hand_result)
                    
                    # Draw hand landmarks
                    for hand_landmarks in results.multi_hand_landmarks:
                        mp_drawing.draw_landmarks(image, hand_landmarks, mp_hands.HAND_CONNECTIONS)
                        
                    # Display current gesture
                    gesture_text = f"Gesture: {gest_name.name if hasattr(gest_name, 'name') else str(gest_name)}"
                    cv2.putText(image, gesture_text, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
                else:
                    Controller.prev_hand = None
                
                # Add instructions on screen
                cv2.putText(image, "Jerry's Virtual Mouse", (10, image.shape[0] - 80), 
                           cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
                cv2.putText(image, "Press ENTER to quit", (10, image.shape[0] - 50), 
                           cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
                cv2.putText(image, f"Frame: {frame_count}", (10, image.shape[0] - 20), 
                           cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
                
                # Display the image
                cv2.imshow('Gesture Controller - Jerry Welcomes you!', image)
                
                # Check for ENTER key (13) to quit
                key = cv2.waitKey(1) & 0xFF
                if key == 13:  # ENTER key
                    print("Exiting...")
                    break
                elif key == ord('q'):  # Also allow 'q' to quit
                    print("Exiting...")
                    break
        
        # Cleanup
        if GestureController.cap:
            GestureController.cap.release()
        cv2.destroyAllWindows()
        print("Virtual Mouse stopped. Jerry says goodbye!")

# Main execution
if __name__ == "__main__":
    try:
        gc1 = GestureController()
        gc1.start()
    except KeyboardInterrupt:
        print("\nInterrupted by user")
    except Exception as e:
        print(f"An error occurred: {e}")
        print("Make sure all required libraries are installed:")
        print("pip install opencv-python mediapipe pyautogui pycaw comtypes screen-brightness-control")
    finally:
        cv2.destroyAllWindows()