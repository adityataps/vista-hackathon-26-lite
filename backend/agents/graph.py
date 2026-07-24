import os

from langchain_aws import ChatBedrockConverse as ChatBedrock
from langgraph.graph import END, START, StateGraph

from agents.nodes.compliance import compliance_node
from agents.nodes.dispatch import dispatch_node
from agents.nodes.intake import intake_node
from agents.nodes.investigate import investigate_node
from agents.nodes.report import report_node
from agents.nodes.resolution import resolution_node
from agents.nodes.technical import technical_node
from agents.state import InvestigationState


def build_graph(llm: ChatBedrock):
    builder = StateGraph(InvestigationState)

    async def _intake(s): return await intake_node(s, llm)
    async def _investigate(s): return await investigate_node(s, llm)
    async def _technical(s): return await technical_node(s, llm)
    async def _compliance(s): return await compliance_node(s, llm)
    async def _resolution(s): return await resolution_node(s, llm)
    async def _report(s): return await report_node(s, llm)

    builder.add_node("intake", _intake)
    builder.add_node("investigate", _investigate)
    builder.add_node("technical", _technical)
    builder.add_node("compliance", _compliance)
    builder.add_node("resolution", _resolution)
    builder.add_node("report", _report)

    builder.add_edge(START, "intake")
    builder.add_edge("intake", "investigate")
    builder.add_conditional_edges("investigate", dispatch_node)
    builder.add_edge("technical", "resolution")
    builder.add_edge("compliance", "resolution")
    builder.add_edge("resolution", "report")
    builder.add_edge("report", END)

    return builder.compile()


def make_llm() -> ChatBedrock:
    return ChatBedrock(
        model_id=os.environ.get(
            "BEDROCK_MODEL_ID",
            "us.anthropic.claude-haiku-4-5-20251001-v1:0",
        ),
        region_name=os.environ.get("AWS_REGION", "us-east-1"),
    )
