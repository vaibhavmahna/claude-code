import sys

from app.text import title_case


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    print(title_case(" ".join(args)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
