"""
カメラ着席判定サンプル(動き検出方式・最小版)
==========================================
Webカメラから一定間隔で1フレームだけ取得し、前回フレームとの差分で
「動きがあったか(=人がいる/活動している)」を判定するだけのサンプルです。

特徴:
    - 顔認識やAIモデルは使わず、ピクセル差分のみで判定するので軽量
    - 常時カメラ映像を処理し続けるのではなく、CHECK_INTERVAL_SEC ごとに
      1枚だけ撮影して比較するので、CPU/カメラ負荷を最小限に抑えられる
    - プレビューウィンドウは表示しない(裏で静かに動く)

必要なライブラリ:
    pip install opencv-python

実行:
    python camera_presence_sample.py
    (Ctrl+C で終了)
"""

import time
import cv2

# ---------------- 設定値 ----------------
CHECK_INTERVAL_SEC = 5      # 何秒おきにフレームを取得して比較するか
MOTION_THRESHOLD = 25       # ピクセル差分の二値化しきい値(0-255、大きいほど鈍感)
MOTION_PIXEL_RATIO = 0.01   # 全体に対して「変化した」とみなすピクセル割合(1% = 0.01)
CAMERA_INDEX = 0            # 使用するカメラ番号(複数ある場合は0,1,2...を試す)


def grab_gray_frame(cap):
    """カメラから1フレーム取得し、グレースケールで縮小して返す。"""
    ok, frame = cap.read()
    if not ok:
        return None
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    # 処理を軽くするため小さく縮小(判定には十分な解像度)
    small = cv2.resize(gray, (160, 120))
    # ノイズの影響を減らすため軽くぼかす
    small = cv2.GaussianBlur(small, (5, 5), 0)
    return small


def detect_motion(prev_frame, curr_frame):
    """2フレーム間の差分から「動きがあったか」を判定する。"""
    diff = cv2.absdiff(prev_frame, curr_frame)
    _, thresh = cv2.threshold(diff, MOTION_THRESHOLD, 255, cv2.THRESH_BINARY)
    changed_pixels = cv2.countNonZero(thresh)
    total_pixels = thresh.shape[0] * thresh.shape[1]
    ratio = changed_pixels / total_pixels
    return ratio >= MOTION_PIXEL_RATIO, ratio


def main():
    cap = cv2.VideoCapture(CAMERA_INDEX)
    if not cap.isOpened():
        print("カメラを開けませんでした。CAMERA_INDEXを変更して試してください。")
        return

    print("カメラ着席判定サンプルを開始します。(Ctrl+C で終了)")
    print(f"{CHECK_INTERVAL_SEC}秒おきにフレームを比較します。\n")

    prev_frame = grab_gray_frame(cap)
    if prev_frame is None:
        print("最初のフレーム取得に失敗しました。")
        cap.release()
        return

    try:
        while True:
            time.sleep(CHECK_INTERVAL_SEC)
            curr_frame = grab_gray_frame(cap)
            if curr_frame is None:
                print("フレーム取得に失敗しました。スキップします。")
                continue

            moved, ratio = detect_motion(prev_frame, curr_frame)
            timestamp = time.strftime("%H:%M:%S")

            if moved:
                print(f"[{timestamp}] 在席(動きあり)  変化率={ratio:.3%}")
            else:
                print(f"[{timestamp}] 離席かも(動きなし) 変化率={ratio:.3%}")

            prev_frame = curr_frame

    except KeyboardInterrupt:
        print("\n終了します。")
    finally:
        cap.release()


if __name__ == "__main__":
    main()