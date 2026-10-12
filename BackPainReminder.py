"""
腰痛防止リマインダー
====================
PCに座り続けている時間を監視し、30分経過したら警告ダイアログを表示します。
タスクトレイに常駐し、マウスが3分間動かなければ「離席した」とみなして
タイマーをリセットします。

必要なライブラリ:
    pip install pynput pystray pillow

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
from tkinter import messagebox

from pynput import mouse
import pystray
from PIL import Image, ImageDraw

try:
    import winsound  # Windows標準ライブラリ
except ImportError:
    winsound = None  # Windows以外ではサウンド再生をスキップ

# ---------------- 設定値 ----------------
WARNING_INTERVAL_SEC = 30 * 60   # 30分: これだけ座り続けたら警告
RESET_IDLE_SEC = 3 * 60          # 3分: これだけマウスが動かなければリセット
CHECK_INTERVAL_SEC = 10          # 状態チェックの間隔(秒)
ALARM_DURATION_SEC = 30          # 警告音を鳴らし続ける長さ(秒)

# Windows標準のシステムサウンド名。お好みで変更可能:
#   "SystemAsterisk" / "SystemExclamation" / "SystemHand" / "SystemQuestion"
WARNING_SOUND = "SystemExclamation"

# ---------------- 内部状態 ----------------
last_mouse_move = None   # 最後にマウスが動いた時刻 (time.time())
session_start = None     # 連続着席タイマーの開始時刻
running = True
root = None               # tkinter のルートウィンドウ
tray_icon = None          # pystray のアイコンオブジェクト(タイトル更新用)
lock = threading.Lock()

TRAY_BASE_TITLE = "腰痛予防タイマー"


def on_move(x, y):
    """マウスが動くたびに呼ばれる。タイマーが止まっていれば開始する。"""
    global last_mouse_move, session_start
    with lock:
        now = time.time()
        last_mouse_move = now
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
    """CHECK_INTERVAL_SEC秒ごとに状態をチェックし、リセット判定と経過判定を行う。"""
    global session_start, last_mouse_move
    while running:
        time.sleep(CHECK_INTERVAL_SEC)
        with lock:
            if session_start is None:
                update_tray_title(f"{TRAY_BASE_TITLE}\n計測待機中(マウスを動かしてください)")
                continue
            now = time.time()

            # 3分間マウスが動いていなければ離席とみなしリセット
            if last_mouse_move is not None and (now - last_mouse_move) > RESET_IDLE_SEC:
                session_start = None
                last_mouse_move = None
                update_tray_title(f"{TRAY_BASE_TITLE}\n計測待機中(マウスを動かしてください)")
                continue

            elapsed = now - session_start
            remaining = WARNING_INTERVAL_SEC - elapsed

            # 30分連続で座っていたら警告
            if elapsed >= WARNING_INTERVAL_SEC:
                session_start = now  # 次の30分の計測を開始
                show_warning()
            else:
                update_tray_title(f"{TRAY_BASE_TITLE}\n残り {format_remaining(remaining)}")


def show_warning():
    """別スレッドからメインスレッド(tkinter)に表示を依頼する。"""
    if root is not None:
        root.after(0, _show_dialog)


def start_warning_sound():
    """
    Windows標準のシステムサウンドをループ再生する。
    (標準の時計アプリのタイマーと同様、止めるまで鳴り続ける)
    """
    if winsound is None:
        return
    try:
        winsound.PlaySound(
            WARNING_SOUND,
            winsound.SND_ALIAS | winsound.SND_ASYNC | winsound.SND_LOOP
        )
    except RuntimeError:
        pass  # サウンドが見つからない等の場合は無視


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
    root.attributes("-topmost", True)
    warning_minutes = WARNING_INTERVAL_SEC // 60
    try:
        messagebox.showwarning(
            "腰痛予防のお知らせ",
            f"{warning_minutes}分座り続けています。\n少し立ち上がって体を伸ばしましょう！"
        )
    finally:
        # OKがクリックされた(=ダイアログが閉じられた)タイミングで音を止める
        timeout_timer.cancel()
        stop_warning_sound()
        root.attributes("-topmost", False)


def make_icon_image():
    """トレイアイコン用の簡単な画像を生成する。"""
    img = Image.new("RGB", (64, 64), "white")
    d = ImageDraw.Draw(img)
    d.ellipse((8, 8, 56, 56), fill="#4caf50")
    return img


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
        f"{TRAY_BASE_TITLE}\n計測待機中(マウスを動かしてください)",
        menu=pystray.Menu(
            pystray.MenuItem("終了", on_quit)
        )
    )
    tray_icon.run()


def main():
    global root
    root = tk.Tk()
    root.withdraw()  # メインウィンドウは表示しない

    # マウス監視リスナーを開始(別スレッドで自動的に動く)
    listener = mouse.Listener(on_move=on_move)
    listener.start()

    # タイマー監視ループを別スレッドで開始
    threading.Thread(target=checker_loop, daemon=True).start()

    # トレイアイコンを別スレッドで起動
    threading.Thread(target=setup_tray, daemon=True).start()

    root.mainloop()


if __name__ == "__main__":
    main()