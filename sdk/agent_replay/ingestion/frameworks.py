"""Thin framework adapters. Prefer exported OTel/OpenInference spans when available."""
from .raw import import_spans

def import_openinference(spans,directory):
    return import_spans(spans,directory,source="openinference")

def import_langgraph(spans,directory):
    # LangGraph traces exported via OTel/LangSmith-compatible span dictionaries.
    return import_spans(spans,directory,source="langgraph")

def import_openai_agents(spans,directory):
    # OpenAI Agents traces represented as span dictionaries; provider-specific attrs are retained.
    return import_spans(spans,directory,source="openai-agents")
