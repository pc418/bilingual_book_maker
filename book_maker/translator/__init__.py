from book_maker.translator.caiyun_translator import Caiyun
from book_maker.translator.chatgptapi_translator import ChatGPTAPI
from book_maker.translator.claude_translator import Claude
from book_maker.translator.custom_api_translator import CustomAPI
from book_maker.translator.deepl_translator import DeepL
from book_maker.translator.deepl_free_translator import DeepLFree
from book_maker.translator.google_translator import Google
from book_maker.translator.tencent_transmart_translator import TencentTranSmart

# A translator is chosen by the *wire format* its endpoint speaks, never by a
# model name. `openai` and `anthropic` reach any host serving those shapes —
# vendor, gateway, or something on localhost — and the model is whatever
# --model_list names. The rest are fixed-endpoint machine-translation
# services that speak only their own protocol and take no model at all.
FORMAT_DICT = {
    "openai": ChatGPTAPI,
    "anthropic": Claude,
    "google": Google,
    "caiyun": Caiyun,
    "deepl": DeepL,
    "deeplfree": DeepLFree,
    "tencent": TencentTranSmart,
    "customapi": CustomAPI,
}

# Formats that talk to a model and therefore need one named.
LLM_FORMATS = ("openai", "anthropic")
