"""轻量冒烟：逐项验证导航是否进到目标页（需手机已授权 USB）。"""
import sys
import time
import uiautomator2 as u2
from core.wechat_nav import start_wechat, goto_tab, ocr_find_and_click, MOMENTS_ENTRY, CHANNELS_ENTRY, click_ratio

SERIAL = sys.argv[1] if len(sys.argv) > 1 else None


def main():
    d = u2.connect(SERIAL) if SERIAL else u2.connect()
    print("device", d.serial, d.window_size())
    d.screen_on()
    ok = start_wechat(d, wait=3, cold=True)
    print("start_wechat", ok, d.app_current())
    if not ok:
        return 1

    goto_tab(d, "discover")
    time.sleep(1)

    import cv2, numpy as np
    from utils.ocr_utils import create_easyocr_reader
    reader = create_easyocr_reader()

    def enhance(g):
        return cv2.createCLAHE(3.0, (8, 8)).apply(g)

    # 朋友圈入口
    hit = ocr_find_and_click(d, reader, ["朋友圈"], y_min_ratio=0.08, y_max_ratio=0.5, enhance=enhance)
    print("moments OCR", hit)
    if not hit:
        click_ratio(d, *MOMENTS_ENTRY)
        print("moments fallback click", MOMENTS_ENTRY)
    time.sleep(2)
    d.screenshot("screenshots/smoke_moments.png")
    print("saved smoke_moments.png")
    d.press("back")
    time.sleep(1)

    # 视频号
    goto_tab(d, "discover")
    time.sleep(1)
    hit = ocr_find_and_click(d, reader, ["视频号"], y_min_ratio=0.08, y_max_ratio=0.55, enhance=enhance)
    print("channels OCR", hit)
    if not hit:
        click_ratio(d, *CHANNELS_ENTRY)
        print("channels fallback", CHANNELS_ENTRY)
    time.sleep(2)
    d.screenshot("screenshots/smoke_channels.png")
    print("saved smoke_channels.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
