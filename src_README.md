# Sentinel — 源代码结构

## 目录布局

```
finance/
├── docs/                     设计文档（已存在于 docs/）
├── strategies/               量化策略（独立 Python 包，可单测）
│   ├── indicators.py         共用指标函数（freqtrade 解耦）
│   ├── s1_trend_follow/      S1 趋势跟踪
│   └── s2_momentum_rotation/ S2 动量轮动
├── ai-service/               AI 分析服务（独立 Python 包）
│   ├── app/
│   │   ├── schemas/          LLM 输出 Pydantic schema
│   │   ├── llm/              LLM 客户端 + 结构化抽取
│   │   └── modules/          风控审计、研报、复盘
│   └── tests/                单测（mock LLM，无 API key 也能跑）
├── deploy/                   Docker 部署
├── pytest.ini                测试配置
└── README.md
```

## 跑测试

```bash
source .venv/bin/activate
python -m pytest                 # 全部
python -m pytest strategies/     # 只跑策略
python -m pytest ai-service/     # 只跑 AI 服务
```

## 设计约束

- **策略层纯逻辑 + freqtrade adapter 分层**：所有计算函数（指标、入场/出场信号、轮动）都用纯 pandas，可独立测试；freqtrade `IStrategy` 类只在镜像可用时 import。
- **AI 服务 LLM 调用全 mock**：所有 LLM 测试用 `FakeLLMClient` 注入，不需 API key 也能完整跑测试。
- **Fail-open 铁律**：LLM 不可达 → 默认 PASS（绝不阻塞交易）。由 `test_llm_unavailable_defaults_to_pass` 单测强制保证。
- **ADR 优先级高于实现**：见 `docs/system/01-architecture.md` 和 `docs/system/04-handoff-guide.md`。