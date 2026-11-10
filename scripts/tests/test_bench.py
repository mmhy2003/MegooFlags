from bench_sdk_evaluate import parse_args


def test_bench_args():
    args = parse_args(["--url", "u", "--key", "k"])
    assert args.url == "u"
    assert args.key == "k"
    assert args.rps == 2000
    assert args.duration == 30
    assert args.concurrency == 512
    assert args.flags is None


def test_bench_args_flags():
    args = parse_args(["--url", "u", "--key", "k", "--rps", "50", "--flags", "a,b"])
    assert args.rps == 50
    assert args.flags == ["a", "b"]
