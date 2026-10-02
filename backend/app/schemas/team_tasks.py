"""Public task API contracts. Unknown fields (including actor spoofing) are rejected."""
from datetime import date
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Actor = Literal['salar', 'chief_of_staff', 'jimmy', 'merchandiser', 'blogger', 'social', 'price_analyst', 'code_improver']
Status = Literal['todo', 'in_progress', 'blocked', 'waiting_on_salar', 'done', 'dropped']
Risk = Literal['spending', 'external_send', 'deletion', 'live_prices']
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=10000)]


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid')


class TaskLink(Contract):
    kind: Literal['product', 'collection', 'page', 'blog_article', 'opportunity_task', 'task', 'pr', 'file', 'url']
    label: str = Field(default='', max_length=300)
    id: str = Field(default='', max_length=300)
    handle: str = Field(default='', max_length=500)
    blog_handle: str = Field(default='', max_length=500)
    url: str = Field(default='', max_length=2000)

    @model_validator(mode='after')
    def reference(self):
        if not any(v.strip() for v in (self.id, self.handle, self.url)):
            raise ValueError('A link needs an ID, handle or URL')
        if self.kind in ('task', 'opportunity_task') and (not self.id.isdigit() or int(self.id) < 1):
            raise ValueError('Task links require a positive task ID')
        if self.url and not self.url.startswith(('https://', 'http://')):
            raise ValueError('URLs must use http or https; store file paths in id')
        return self


class Measurement(Contract):
    metric: str = Field(default='', max_length=300)
    baseline: str = Field(default='', max_length=2000)
    recheck_on: date | None = None
    result: str = Field(default='', max_length=4000)


class CreateTask(Contract):
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=250)]
    outcome: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
    owner: Actor
    priority: Literal['P0', 'P1', 'P2', 'P3'] = 'P2'
    due_on: date | None = None
    check_by: date | None = None
    blocked_by: list[int] = Field(default_factory=list, max_length=100)
    blocked_reason: str = Field(default='', max_length=2000)
    links: list[TaskLink] = Field(default_factory=list, max_length=100)
    measurement: Measurement | None = None
    risks: list[Risk] = Field(default_factory=list, max_length=4)
    authorization: str = Field(default='', max_length=2000)


class Versioned(Contract):
    version: int = Field(ge=1)


class PatchTask(Versioned):
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=250)] | None = None
    outcome: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)] | None = None
    owner: Actor | None = None
    priority: Literal['P0', 'P1', 'P2', 'P3'] | None = None
    due_on: date | None = None
    check_by: date | None = None
    blocked_by: list[int] | None = Field(default=None, max_length=100)
    blocked_reason: str | None = Field(default=None, max_length=2000)
    links: list[TaskLink] | None = Field(default=None, max_length=100)
    measurement: Measurement | None = None
    risks: list[Risk] | None = Field(default=None, max_length=4)
    authorization: str | None = Field(default=None, max_length=2000)
    proof: str | None = Field(default=None, max_length=10000)
    note: str = Field(default='', max_length=10000)

    @model_validator(mode='after')
    def nonnullable(self):
        for key in self.model_fields_set - {'due_on', 'check_by', 'measurement'}:
            if getattr(self, key) is None:
                raise ValueError(f'{key} cannot be null')
        return self


class ChangeStatus(Versioned):
    status: Status
    note: str = Field(default='', max_length=10000)
    proof: str = Field(default='', max_length=10000)
    question: str = Field(default='', max_length=2000)
    options: list[Text] = Field(default_factory=list, max_length=10)


class AddNote(Versioned):
    note: Text


class Decision(Versioned):
    answer: str = Field(default='', max_length=10000)
    note: str = Field(default='', max_length=2000)
    approved: bool | None = None
