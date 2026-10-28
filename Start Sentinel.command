#!/bin/zsh
# Historical replay only; no .env loading and no trading command.
cd "${0:A:h}" || exit 1
if [[ ! -x .venv-engine/bin/python ]]; then
    print '尚未完成一次性运行环境安装，请参阅 PRACTICE.md。'
    read '?按回车关闭。'
    exit 1
fi
.venv-engine/bin/python -m practice.run --open
result=$?
if (( result != 0 )); then
    read '?回放未完成，按回车关闭。'
fi
exit $result
