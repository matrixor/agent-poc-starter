# services/mcp-fin-analyst/app/finance_crew.py
from __future__ import annotations
from typing import List
import os
from pydantic import BaseModel
from crewai import Agent, Task, Crew, Process
from crewai_tools import CodeInterpreterTool
from langchain_openai import ChatOpenAI

class QueryAnalysisOutput(BaseModel):
    symbols: List[str]
    timeframe: str  # e.g. '6mo','1y','ytd'
    action: str     # e.g. 'plot_performance'

llm = ChatOpenAI(model=os.getenv("OPENAI_MODEL", "gpt-4o"), temperature=0)

# PoC: run code inside the container (no docker-in-docker)
code_runner = CodeInterpreterTool(unsafe_mode=True)

query_parser = Agent(
    role="Stock Data Analyst",
    goal=(
        "从用户自然语言中提取股票代码(symbols)、时间区间(timeframe)与动作(action)，"
        "确保输出符合 Pydantic 模型 QueryAnalysisOutput"
    ),
    backstory="你熟悉美股常见代码、日期表达与基础技术指标。",
    llm=llm,
    output_pydantic=QueryAnalysisOutput,
)

code_writer = Agent(
    role="Senior Python Developer",
    goal=(
        "编写一个可直接运行的 Python 脚本 `stock_analysis.py`，使用 yfinance+pandas+matplotlib "
        "下载并绘制收盘价曲线，保存图为 output.png 到工作目录。不要尝试 pip 安装依赖。"
        "在脚本开头设置无头绘图：\n"
        "import matplotlib; matplotlib.use('Agg')\n"
    ),
    backstory="你注重健壮性，遇到空数据/无效代码要给出合理报错。",
    llm=llm,
    tools=[code_runner],
)

query_task = Task(
    description=(
        "解析用户查询：{query}\n"
        "产出 JSON，字段：symbols(List[str])、timeframe(str)、action(str)。"
        "timeframe 示例：'6mo'、'1y'、'ytd'。"
        "若未明确动作，action 用 'plot_performance'。"
    ),
    agent=query_parser,
    expected_output="一个 JSON，匹配 QueryAnalysisOutput"
)

code_task = Task(
    description=(
        "根据解析结果：{parsed}\n"
        "生成脚本 `stock_analysis.py`，要求：\n"
        "1) 使用 yfinance 下载 {parsed.symbols} 在 {parsed.timeframe} 的历史数据；\n"
        "2) 画收盘价曲线（多只股票对比放在一张图）；\n"
        "3) 保存为 output.png；\n"
        "4) 运行脚本以生成图片（使用可用的代码执行工具）。"
    ),
    agent=code_writer,
    tools=[code_runner],
    expected_output="已生成 output.png"
)

run_task = Task(
    description="如果上一任务未成功生成图片，请修正代码并再次运行，直到 output.png 存在或给出清晰错误。",
    agent=code_writer,
    tools=[code_runner],
    expected_output="确认 output.png 存在"
)

crew = Crew(
    agents=[query_parser, code_writer],
    tasks=[query_task, code_task, run_task],
    process=Process.sequential,
)

def run_financial_analysis(query: str) -> str:
    os.makedirs("/app/app/out", exist_ok=True)
    result = crew.kickoff(inputs={"query": query, "parsed": ""})
    return "app/out/output.png"
