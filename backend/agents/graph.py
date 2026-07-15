import os

from langchain_aws import ChatBedrock
from langgraph.graph import StateGraph, START, END

from agents.state import InvestigationState
from agents.nodes.intake import intake_node
from agents.nodes.investigate import investigate_node
from agents.nodes.dispatch import dispatch_node
from agents.nodes.technical import technical_node
from agents.nodes.compliance import compliance_node
from agents.nodes.resolution import resolution_node


def build_graph(llm: ChatBedrock):
    builder = StateGraph(InvestigationState)

    # Bind llm into each node via async closures so LangGraph detects them as
    # coroutine functions and awaits them — sync lambdas cause run_in_executor to
    # return the coroutine unawaited, breaking the state-write step.
    async def _intake(s): return await intake_node(s, llm)
    async def _investigate(s): return await investigate_node(s, llm)
    async def _technical(s): return await technical_node(s, llm)
    async def _compliance(s): return await compliance_node(s, llm)
    async def _resolution(s): return await resolution_node(s, llm)

    builder.add_node("intake", _intake)
    builder.add_node("investigate", _investigate)
    builder.add_node("technical", _technical)
    builder.add_node("compliance", _compliance)
    builder.add_node("resolution", _resolution)

    builder.add_edge(START, "intake")
    builder.add_edge("intake", "investigate")
    # dispatch_node returns list[Send] — used directly as routing function (NOT registered as a node)
    builder.add_conditional_edges("investigate", dispatch_node)
    builder.add_edge("technical", "resolution")
    builder.add_edge("compliance", "resolution")
    builder.add_edge("resolution", END)

    return builder.compile()


def make_llm() -> ChatBedrock:
    return ChatBedrock(
        model_id="anthropic.claude-sonnet-4-6",
        region_name=os.environ.get("AWS_REGION", "us-west-2"),
    )
