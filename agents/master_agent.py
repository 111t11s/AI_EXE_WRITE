"""CrewAI 主智能体的最小运行示例。

从项目根目录运行：python -m agents.master_agent
"""

import os

from crewai import Agent, Crew, LLM, Process, Task

from core.config import get_settings


def create_master_agent() -> Agent:
    """创建负责小说创作规划的测试智能体。"""
    settings = get_settings()
    if not settings.API_KEY:
        raise RuntimeError("请先在 .env 中配置 API_KEY")

    model_name = os.getenv("MODEL_NAME", settings.DEEPSEEK_MODEL_NAME)
    llm = LLM(
        model=model_name,
        api_key=settings.API_KEY,
        base_url=settings.PROXY_URL,
        timeout=settings.REQUEST_TIMEOUT or 120,
        max_retries=0,
        stream=False,
    )
    return Agent(
        role="小说创作总策划",
        goal="根据创作需求给出简明、可执行的小说构思",
        backstory="你熟悉中文网络小说的题材、冲突设计和故事节奏。",
        llm=llm,
        verbose=True,
        allow_delegation=False,
        max_iter=1,
        max_retry_limit=0,
    )


def run_test(topic: str = "一个普通人意外获得改变命运的能力") -> str:
    """运行一次简单任务，验证 CrewAI 与模型接口可以正常工作。"""
    agent = create_master_agent()
    task = Task(
        description=(
            f"围绕主题“{topic}”构思一部中文网络小说。"
            "给出书名、一句话梗概、主角目标、核心冲突和前三章剧情要点。"
        ),
        expected_output="包含书名、梗概、主角目标、核心冲突和前三章要点的中文方案",
        agent=agent,
    )
    crew = Crew(agents=[agent], tasks=[task], process=Process.sequential, verbose=True)
    return str(crew.kickoff())


if __name__ == "__main__":
    print(run_test())



