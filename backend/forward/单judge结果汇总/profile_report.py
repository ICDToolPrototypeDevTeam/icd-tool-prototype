import json, time, cProfile, pstats, io, os, glob
import summary


def latest_run_dir():
    base = os.path.dirname(os.path.abspath(__file__))
    out_root = os.path.join(base, "outputs")
    if not os.path.isdir(out_root):
        return None
    cands = [d for d in glob.glob(os.path.join(out_root, "*EoICD到软件高层需求的落实检查_*"))
             if os.path.isdir(d)]
    if not cands:
        return None
    return max(cands, key=lambda d: os.path.getmtime(d))


def run(direction):
    rd = latest_run_dir()
    if rd is None:
        print("[跳过] 未找到 outputs 下任意运行文件夹")
        return
    jpath = os.path.join(rd, "report_%s.json" % direction)
    if not os.path.isfile(jpath):
        print("[跳过] 缺 %s" % jpath)
        return
    out = json.load(open(jpath, encoding="utf-8"))
    pr = cProfile.Profile()
    pr.enable()
    t0 = time.time()
    # 写入临时目录，避免污染运行文件夹
    tmp_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_profile_tmp")
    os.makedirs(tmp_dir, exist_ok=True)
    summary.write_docx_summary(out, os.path.join(tmp_dir, "summary_%s.docx" % direction))
    summary.write_xlsx_summary(out, os.path.join(tmp_dir, "summary_%s.xlsx" % direction))
    t1 = time.time()
    pr.disable()
    print("=== %s (from %s) ===" % (direction, os.path.basename(rd)))
    print("wall time: %.1fs" % (t1 - t0))
    s = io.StringIO()
    ps = pstats.Stats(pr, stream=s).sort_stats("cumulative")
    ps.print_stats(15)
    print(s.getvalue())


if __name__ == "__main__":
    run("pub")
    run("sub")
