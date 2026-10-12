"""
腰痛防止リマインダー(カメラ着席判定版)
====================================
PCに座り続けている時間を監視し、30分経過したら警告ダイアログを表示します。
タスクトレイに常駐し、Webカメラの映像から「動き」を検出することで着席状態を
判定します。3分間動きが検出されなければ「離席した」とみなしてタイマーを
リセットします。

判定方式:
    一定間隔(CHECK_INTERVAL_SEC)でカメラから1フレームだけ取得し、
    前回フレームとの差分(ピクセル変化量)で動きの有無を判定します。
    顔認識やAIモデルは使わず、OpenCVの差分計算のみなので軽量です。
    プレビューウィンドウは表示せず、裏で静かに動作します。

必要なライブラリ:
    pip install opencv-python pystray pillow

警告音には Windows 標準の winsound モジュールを使用しています
(追加インストール不要、Windows専用)。

実行:
    python back_pain_reminder.py

終了方法:
    タスクトレイのアイコンを右クリック → 「終了」
"""

import threading
import time
import tkinter as tk

import cv2
import pystray
from PIL import Image, ImageDraw

try:
    import winsound  # Windows標準ライブラリ
except ImportError:
    winsound = None  # Windows以外ではサウンド再生をスキップ

# ---------------- 設定値 ----------------
WARNING_INTERVAL_SEC = 30 * 60   # 30分: これだけ座り続けたら警告
RESET_IDLE_SEC = 2 * 60          # 3分: これだけ動きが検出されなければリセット
CHECK_INTERVAL_SEC = 10          # 状態チェック(カメラ撮影)の間隔(秒)
RECENT_MOTION_WINDOW_SEC = 10    # 30分経過時、この秒数以内に動きがあれば
                                  # 「離席判定タイマー未作動」とみなす(即ダイアログ)
ALARM_DURATION_SEC = 20          # 警告音を鳴らし続ける長さ(秒)
DIALOG_TIMEOUT_SEC = 60          # ダイアログを自動で閉じるまでの時間(秒)

# Windows標準のシステムサウンド名。お好みで変更可能:
#   "SystemAsterisk" / "SystemExclamation" / "SystemHand" / "SystemQuestion"
WARNING_SOUND = "SystemExclamation"

# カメラ動き検出のパラメータ
CAMERA_INDEX = 0             # 使用するカメラ番号(複数ある場合は0,1,2...を試す)
MOTION_THRESHOLD = 25        # ピクセル差分の二値化しきい値(0-255、大きいほど鈍感)
MOTION_PIXEL_RATIO = 0.01    # 全体に対して「変化した」とみなすピクセル割合(1% = 0.01)
BRIGHTNESS_THRESHOLD = 100    # 平均輝度がこの値以下なら「暗い=離席」とみなす(0-255)

# ---------------- 内部状態 ----------------
last_motion_time = None  # 最後に動きが検出された時刻 (time.time())
session_start = None     # 連続着席タイマーの開始時刻
pending_confirmation = False  # 30分経過検知後、離席判定タイマー作動中で
                               # ダイアログ表示を保留している状態かどうか
running = True
root = None               # tkinter のルートウィンドウ
tray_icon = None          # pystray のアイコンオブジェクト(タイトル更新用)
lock = threading.Lock()

TRAY_BASE_TITLE = "腰痛予防タイマー"
TRAY_WAITING_TEXT = f"{TRAY_BASE_TITLE}\n計測待機中(カメラの前で動いてください)"


def grab_gray_frame(cap):
    """カメラから1フレーム取得し、グレースケールで縮小・平滑化して返す。"""
    ok, frame = cap.read()
    if not ok:
        return None
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(gray, (160, 120))           # 軽量化のため縮小
    small = cv2.GaussianBlur(small, (5, 5), 0)      # ノイズ低減
    return small


def detect_motion(prev_frame, curr_frame):
    """2フレーム間の差分から「動きがあったか」を判定する。"""
    diff = cv2.absdiff(prev_frame, curr_frame)
    _, thresh = cv2.threshold(diff, MOTION_THRESHOLD, 255, cv2.THRESH_BINARY)
    changed_pixels = cv2.countNonZero(thresh)
    total_pixels = thresh.shape[0] * thresh.shape[1]
    ratio = changed_pixels / total_pixels
    return ratio >= MOTION_PIXEL_RATIO


def on_motion_detected():
    """動きが検出されたときに呼ばれる。タイマーが止まっていれば開始する。
    (呼び出し元で lock 取得済みの前提)
    """
    global last_motion_time, session_start
    now = time.time()
    last_motion_time = now
    if session_start is None:
        session_start = now


def format_remaining(seconds):
    """残り秒数を「mm:ss」形式の文字列に整形する。"""
    seconds = max(0, int(seconds))
    minutes, secs = divmod(seconds, 60)
    return f"{minutes:02d}:{secs:02d}"


def update_tray_title(text):
    if tray_icon is not None:
        tray_icon.title = text


def checker_loop():
    """
    CHECK_INTERVAL_SECごとにカメラで1フレーム撮影し、動き検出と
    タイマー判定(リセット/30分経過/保留)をまとめて行う。
    """
    global session_start, last_motion_time, pending_confirmation

    cap = cv2.VideoCapture(CAMERA_INDEX)
    if not cap.isOpened():
        update_tray_title(f"{TRAY_BASE_TITLE}\nカメラを開けませんでした")
        return

    prev_frame = grab_gray_frame(cap)

    try:
        while running:
            time.sleep(CHECK_INTERVAL_SEC)

            curr_frame = grab_gray_frame(cap)
            if curr_frame is None:
                continue  # フレーム取得失敗時は今回の判定をスキップ

            # 輝度チェック: 暗すぎる場合はノイズ誤検知を防ぐため動き判定をスキップ
            # prev_frameもリセットして、再点灯後に消灯前のフレームと比較しないようにする
            brightness = float(cv2.mean(curr_frame)[0])
            if brightness < BRIGHTNESS_THRESHOLD:
                prev_frame = None
                continue

            moved = False
            if prev_frame is not None:
                moved = detect_motion(prev_frame, curr_frame)
            prev_frame = curr_frame

            with lock:
                if moved:
                    on_motion_detected()

                if session_start is None:
                    # 待機中(まだ着席タイマーが始まっていない)
                    pending_confirmation = False
                    update_tray_icon(seated=False)
                    update_tray_title(TRAY_WAITING_TEXT)
                    continue

                now = time.time()

                # --- 保留(判定待ち)中の処理 ---
                if pending_confirmation:
                    if moved:
                        # 確定前に動きを検出 = 離席していなかった → 即ダイアログ
                        pending_confirmation = False
                        session_start = now  # 次の30分計測を開始
                        show_warning()
                        continue

                    if (now - last_motion_time) > RESET_IDLE_SEC:
                        # 離席判定が確定 → ダイアログなしで待機状態に戻す
                        pending_confirmation = False
                        session_start = None
                        last_motion_time = None
                        update_tray_icon(seated=False)
                        update_tray_title(TRAY_WAITING_TEXT)
                        continue

                    # まだ判定待ち継続中
                    update_tray_icon(seated=True)
                    update_tray_title(f"{TRAY_BASE_TITLE}\n残り {format_remaining(0)}")
                    continue

                # --- 通常時(保留中でない)の処理 ---

                # 3分間動きが検出されなければ離席とみなしリセット
                if last_motion_time is not None and (now - last_motion_time) > RESET_IDLE_SEC:
                    session_start = None
                    last_motion_time = None
                    update_tray_icon(seated=False)
                    update_tray_title(TRAY_WAITING_TEXT)
                    continue

                elapsed = now - session_start
                remaining = WARNING_INTERVAL_SEC - elapsed

                # 30分連続で座っていたら警告(または保留)判定
                if elapsed >= WARNING_INTERVAL_SEC:
                    # 過去RECENT_MOTION_WINDOW_SEC秒以内に動きがあったか
                    recently_moved = (
                        last_motion_time is not None
                        and (now - last_motion_time) <= RECENT_MOTION_WINDOW_SEC
                    )
                    if recently_moved:
                        # 直近に動きあり = 離席判定タイマー未作動 → 即ダイアログ
                        session_start = now  # 次の30分の計測を開始
                        show_warning()
                    else:
                        # 離席判定タイマーが作動中 → 保留状態に入る
                        pending_confirmation = True
                        update_tray_icon(seated=True)
                        update_tray_title(f"{TRAY_BASE_TITLE}\n残り {format_remaining(0)}")
                else:
                    update_tray_icon(seated=True)
                    update_tray_title(f"{TRAY_BASE_TITLE}\n残り {format_remaining(remaining)}")
    finally:
        cap.release()


def show_warning():
    """別スレッドからメインスレッド(tkinter)に表示を依頼する。"""
    if root is not None:
        root.after(0, _show_dialog)


def start_warning_sound():
    """Windows標準のシステムサウンドをループ再生する。"""
    if winsound is None:
        return
    try:
        winsound.PlaySound(
            WARNING_SOUND,
            winsound.SND_ALIAS | winsound.SND_ASYNC | winsound.SND_LOOP
        )
    except RuntimeError:
        pass


def stop_warning_sound():
    """再生中のループサウンドを止める。"""
    if winsound is None:
        return
    try:
        winsound.PlaySound(None, winsound.SND_PURGE)
    except RuntimeError:
        pass


def schedule_alarm_timeout():
    """ALARM_DURATION_SEC秒後に警告音を自動停止するタイマーを起動する。"""
    timer = threading.Timer(ALARM_DURATION_SEC, stop_warning_sound)
    timer.daemon = True
    timer.start()
    return timer


def _show_dialog():
    start_warning_sound()
    timeout_timer = schedule_alarm_timeout()

    # カスタムダイアログ（非ブロッキング）を作成
    dialog = tk.Toplevel(root)
    dialog.title("腰痛予防のお知らせ")
    dialog.attributes("-topmost", True)
    dialog.resizable(False, False)

    cleaned_up = False
    def close_dialog():
        nonlocal cleaned_up
        if not cleaned_up:
            cleaned_up = True
            timeout_timer.cancel()
            stop_warning_sound()
            dialog.destroy()

    # ユーザーが「×」ボタンで閉じた時の処理
    dialog.protocol("WM_DELETE_WINDOW", close_dialog)

    # 指定秒数（1分）後に自動閉鎖するタイマーをセット
    auto_close_job = root.after(DIALOG_TIMEOUT_SEC * 1000, close_dialog)

    def on_ok():
        root.after_cancel(auto_close_job)
        close_dialog()

    warning_minutes = WARNING_INTERVAL_SEC // 60
    msg = f"{warning_minutes}分座り続けています。\n少し立ち上がって体を伸ばしましょう！"

    label = tk.Label(dialog, text=msg, padx=20, pady=20, font=("Helvetica", 10))
    label.pack()

    ok_button = tk.Button(dialog, text="OK", width=10, command=on_ok)
    ok_button.pack(pady=(0, 15))

    # 画面中央に配置
    dialog.update_idletasks()
    x = (dialog.winfo_screenwidth() - dialog.winfo_reqwidth()) // 2
    y = (dialog.winfo_screenheight() - dialog.winfo_reqheight()) // 2
    dialog.geometry(f"+{x}+{y}")


def make_icon_image(color="#4caf50"):
    """トレイアイコン用の画像を生成する。color: 丸の色"""
    img = Image.new("RGB", (64, 64), "white")
    d = ImageDraw.Draw(img)
    d.ellipse((8, 8, 56, 56), fill=color)
    return img


def update_tray_icon(seated: bool):
    """着席状態に応じてトレイアイコンの丸の色を切り替える。"""
    if tray_icon is not None:
        tray_icon.icon = make_icon_image("#e53935" if seated else "#4caf50")


def on_quit(icon, item):
    global running
    running = False
    icon.stop()
    if root is not None:
        root.after(0, root.quit)


def setup_tray():
    global tray_icon
    tray_icon = pystray.Icon(
        "back_pain_reminder",
        make_icon_image(),
        TRAY_WAITING_TEXT,
        menu=pystray.Menu(
            pystray.MenuItem("終了", on_quit)
        )
    )
    tray_icon.run()


def main():
    global root
    root = tk.Tk()
    root.withdraw()  # メインウィンドウは表示しない

    # タイマー監視ループを別スレッドで開始
    threading.Thread(target=checker_loop, daemon=True).start()

    # トレイアイコンを別スレッドで起動
    threading.Thread(target=setup_tray, daemon=True).start()

    root.mainloop()


if __name__ == "__main__":
    main()