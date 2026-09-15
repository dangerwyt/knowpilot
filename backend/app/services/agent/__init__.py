from langchain.chat_models import init_chat_model
from app.core.config import settings


_model = None
_review_model = None

def generate_model():
    global _model
    if _model is None:
        _model = init_chat_model(
            model=settings.deepseek_planner_model,
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            timeout=60,
            max_retries=3,
            extra_body={
                'thinking': {
                    'type': 'disabled'
                }
            }
        )
    return _model

def generate_review_model():
    global _review_model
    if _review_model is None:
        _review_model = init_chat_model(
            model=settings.deepseek_review_model,
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            timeout=60,
            max_retries=3,
            extra_body={
                'thinking': {
                    'type': 'disabled'
                }
            }
        )
    return _review_model
