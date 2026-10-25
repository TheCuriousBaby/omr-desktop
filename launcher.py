"""打包后的统一入口：带 --engine 参数时作为识别引擎子进程运行，否则启动桌面界面。"""
import multiprocessing
import os
import sys
import tempfile
import traceback


def log_path():
    return os.path.join(tempfile.gettempdir(), "omr_desktop_crash.log")


def bind_std_streams():
    """窗口版程序的标准输出被替换为空写入器，这里改为绑定父进程传入的标准句柄。"""
    if not getattr(sys, "frozen", False):
        return
    import ctypes
    import msvcrt

    kernel32 = ctypes.windll.kernel32
    kernel32.GetStdHandle.restype = ctypes.c_void_p
    for name, std_id in (("stdout", -11), ("stderr", -12)):
        handle = kernel32.GetStdHandle(std_id)
        if not handle or handle == ctypes.c_void_p(-1).value:
            continue
        try:
            fd = msvcrt.open_osfhandle(handle, os.O_WRONLY)
        except OSError:
            continue
        stream = open(fd, "w", encoding="utf-8", errors="replace", buffering=1, closefd=False)
        setattr(sys, name, stream)


def run():
    multiprocessing.freeze_support()
    if len(sys.argv) > 1 and sys.argv[1] == "--engine":
        bind_std_streams()
        from app.engine_runner import main as engine_main

        sys.exit(engine_main(sys.argv[2:]))
    from app.main import main

    main()


if __name__ == "__main__":
    try:
        run()
    except SystemExit:
        raise
    except BaseException:
        with open(log_path(), "a", encoding="utf-8") as handle:
            handle.write(traceback.format_exc() + "\n")
        raise
