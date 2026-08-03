
import win32api, win32con, win32gui, ctypes, ctypes.wintypes, time
from PIL import ImageGrab

hwnd = 2820352

# 激活窗口
win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
win32gui.SetForegroundWindow(hwnd)
time.sleep(0.6)

# 客户区原点 -> 屏幕坐标
cx, cy = win32gui.ClientToScreen(hwnd, (0, 0))
print(f"ClientToScreen origin: {cx},{cy}")

# 取DPI缩放
user32 = ctypes.windll.user32
try:
    user32.SetProcessDPIAware()
except: pass

# 卡片在客户区大约 x=300, y=140 (第一个卡片中央)
target_x = cx + 300
target_y = cy + 140
print(f"Right-click target: {target_x},{target_y}")

# 使用 SendInput 模拟右键
INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_ABSOLUTE = 0x8000

# 屏幕归一化坐标
screen_w = user32.GetSystemMetrics(0)
screen_h = user32.GetSystemMetrics(1)
abs_x = int(target_x * 65535 / screen_w)
abs_y = int(target_y * 65535 / screen_h)

class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long),
                ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]

class INPUT(ctypes.Structure):
    class _INPUT(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT)]
    _anonymous_ = ("_input",)
    _fields_ = [("type", ctypes.c_ulong), ("_input", _INPUT)]

def send_mouse(flags, x=0, y=0):
    inp = INPUT(type=INPUT_MOUSE)
    inp.mi.dx = x; inp.mi.dy = y
    inp.mi.dwFlags = flags
    ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))

# 移动到目标
send_mouse(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, abs_x, abs_y)
time.sleep(0.2)
# 右键按下
send_mouse(MOUSEEVENTF_RIGHTDOWN | MOUSEEVENTF_ABSOLUTE, abs_x, abs_y)
time.sleep(0.05)
send_mouse(MOUSEEVENTF_RIGHTUP | MOUSEEVENTF_ABSOLUTE, abs_x, abs_y)
time.sleep(0.8)  # 等菜单出现

# 截图看菜单
rect = win32gui.GetWindowRect(hwnd)
img = ImageGrab.grab(bbox=(0, 0, 1920, 1080))
img.save(r"D:\task_widget\docs\menu_state.png")
print("menu_state.png saved")

# 找上下文菜单窗口
menus = []
def enum_cb(h, _):
    if win32gui.IsWindowVisible(h):
        cls = win32gui.GetClassName(h)
        if '#32768' in cls or 'Menu' in cls or 'Popup' in cls:
            r = win32gui.GetWindowRect(h)
            menus.append((h, cls, r))
    return True
win32gui.EnumWindows(enum_cb, None)
print("Menus found:", menus)
