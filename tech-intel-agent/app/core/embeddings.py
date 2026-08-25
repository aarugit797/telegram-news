import voyageai
from typing import Literal

from app.core.config import settings


_client = voyageai.AsyncClient(api_key=settings.voyage_api_key)


EMBEDDING_MODEL = "voyage-4"


async def get_embedding(text: str,input_type: Literal["document", "query"] ) -> list[float]:
    """
    Converts a piece of text into a 1024-number vector representing
    its meaning.

    input_type: Voyage's API distinguishes between "document" (text
    being STORED for later search - e.g. a signal's summary when an
    agent writes it) and "query" (text being used to SEARCH - e.g. a
    user's question in the News DB Tool). Voyage embeds these two
    slightly differently under the hood to improve retrieval quality
    - passing the wrong one won't error, but will quietly make
    semantic search less accurate. Callers must be explicit rather
    than us silently defaulting to one.
    """
    result = await _client.embed(
        texts=[text],
        model=EMBEDDING_MODEL,
        input_type=input_type,
    )
    return result.embeddings[0]