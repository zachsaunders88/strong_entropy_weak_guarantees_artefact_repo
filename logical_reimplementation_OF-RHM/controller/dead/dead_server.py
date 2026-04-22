#!/usr/bin/env python3
"""Wrapper entry-point for the DEAD entropy service."""

from .server import serve_forever


def main():
    serve_forever()


if __name__ == "__main__":
    main()