from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

Id = Annotated[int, Field(gt=0, le=2**64 - 1)]


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EmbedField(InputModel):
    name: str = Field(min_length=1, max_length=256)
    value: str = Field(min_length=1, max_length=1024)
    inline: bool = False


class EmbedFooter(InputModel):
    text: str = Field(min_length=1, max_length=2048)
    icon_url: HttpUrl | None = None


class EmbedAuthor(InputModel):
    name: str = Field(min_length=1, max_length=256)
    url: HttpUrl | None = None
    icon_url: HttpUrl | None = None


class EmbedImage(InputModel):
    url: HttpUrl


class Embed(InputModel):
    title: str | None = Field(default=None, min_length=1, max_length=256)
    description: str | None = Field(default=None, min_length=1, max_length=4096)
    url: HttpUrl | None = None
    color: int | None = Field(default=None, ge=0, le=0xFFFFFF)
    fields: list[EmbedField] = Field(default_factory=list, max_length=25)
    footer: EmbedFooter | None = None
    author: EmbedAuthor | None = None
    image: EmbedImage | None = None
    thumbnail: EmbedImage | None = None

    def text_length(self) -> int:
        return (
            len(self.title or "")
            + len(self.description or "")
            + sum(len(field.name) + len(field.value) for field in self.fields)
            + (len(self.footer.text) if self.footer else 0)
            + (len(self.author.name) if self.author else 0)
        )

    @model_validator(mode="after")
    def not_empty(self) -> Self:
        if not any(
            (
                self.title,
                self.description,
                self.fields,
                self.footer,
                self.author,
                self.image,
                self.thumbnail,
            )
        ):
            raise ValueError("Embed must contain visible content")
        return self


class MessageCreate(InputModel):
    content: str | None = Field(default=None, max_length=2000)
    embeds: list[Embed] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def validate_message(self) -> Self:
        if not (self.content and self.content.strip()) and not self.embeds:
            raise ValueError("Provide content or at least one embed")
        if sum(embed.text_length() for embed in self.embeds) > 6000:
            raise ValueError("Embed text exceeds the combined 6000 character limit")
        return self


class RoleAssignment(InputModel):
    member_id: Id
    role_ids: list[Id] = Field(min_length=1, max_length=25)

    @model_validator(mode="after")
    def unique_roles(self) -> Self:
        if len(set(self.role_ids)) != len(self.role_ids):
            raise ValueError("Role IDs must be unique")
        return self
