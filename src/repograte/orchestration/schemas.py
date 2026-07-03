from pydantic import BaseModel, Field
from typing import List


class SearchReplaceBlock(BaseModel):
    search_block: str = Field(
        ...,
        description="Exact existing code to replace. Must match the source file verbatim.",
    )
    replace_block: str = Field(..., description="The new functional component code.")


class EngineerDiffOutput(BaseModel):
    reasoning: str = Field(
        ..., description="Brief explanation of the refactor mapping."
    )
    blocks: List[SearchReplaceBlock] = Field(
        ..., description="List of search/replace operations."
    )


class QAValidationResult(BaseModel):
    is_valid: bool = Field(
        ..., description="True if the diff logic is sound and syntax is valid."
    )
    feedback: str = Field(
        default="", description="If invalid, provide strict feedback on what failed."
    )
