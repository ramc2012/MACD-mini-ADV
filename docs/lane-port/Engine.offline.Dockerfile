# Reuse the tested installed dependencies; no package download or runtime import during build.
FROM macd-mini-parallel-engine:review-fixes-20261025
COPY src /app/src
COPY src/macd_trader /usr/local/lib/python3.12/site-packages/macd_trader
RUN python -c 'import ast,pathlib; paths=list(pathlib.Path("/app/src").rglob("*.py")); [ast.parse(p.read_text(),filename=str(p)) for p in paths]; print("parsed",len(paths),"Python modules")'
