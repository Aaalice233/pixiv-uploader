"""Backward-compatible Web entry point."""

from pixiv_uploader.web import app, main

__all__ = ["app", "main"]


if __name__ == "__main__":
    main()
