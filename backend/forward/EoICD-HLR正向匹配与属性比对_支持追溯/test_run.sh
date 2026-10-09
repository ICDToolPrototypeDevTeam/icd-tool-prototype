#!/bin/bash
cd /root/.openclaw/workspace/projects/EoICD-HLR正向匹配
rm -rf data/output/*
: "${DEEPSEEK_API_KEY:?请先导出环境变量 DEEPSEEK_API_KEY}"
python3 src/match_engine.py
