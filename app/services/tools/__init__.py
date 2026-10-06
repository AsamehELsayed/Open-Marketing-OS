"""Default registry builder (v0.2 §6). One place to assemble all tools."""
from .delegate_tools import register_delegate_tools
from .instagram_tools import register_instagram_tools
from .knowledge_tools import register_knowledge_tools
from .propose_tools import register_propose_tools
from .registry import Registry
from .state_tools import register_state_tools
from .web_tools import register_web_tools, register_website_tools


def build_default_registry(web_transport=None, website_transport=None) -> Registry:
    reg = Registry()
    register_state_tools(reg)
    register_knowledge_tools(reg)
    register_web_tools(reg, transport=web_transport)
    register_website_tools(reg, transport=website_transport)
    register_instagram_tools(reg)
    register_delegate_tools(reg)
    register_propose_tools(reg)
    return reg
