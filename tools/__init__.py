"""저장소에 딸린 도구들.

**꾸러미로 만들어 둔다.** `python tools/candles.py`처럼 스크립트로 부르면
파이썬이 저장소 뿌리가 아니라 `tools/`를 경로에 넣어서, 같은 폴더의 다른
파일을 `from tools.pack import ...`으로 못 읽는다. 실제로 이걸로 판이 한 번
죽었다 — 로컬에서는 PYTHONPATH를 붙여 돌렸던 탓에 안 보였다.

그래서 `python -m tools.candles`로 부른다. 그러면 지금 폴더가 경로에 들어간다.
"""
