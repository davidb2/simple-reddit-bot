#!/usr/bin/env python3
import asyncio
import asyncpraw
import bs4
import datetime as dt
import math
import praw
import praw.models
import re

from pydantic import BaseSettings, SecretStr
from typing import NoReturn, Optional, Pattern

PATTERN = re.compile(r'b[^a-z]*12', re.IGNORECASE)

class RedditBotParams(BaseSettings):
  pattern: Pattern[str]
  """The comment pattern that triggers this bot."""
  reply: str
  """The reply markdown string."""
  subreddit: str
  """The name of the subreddit to stream comments from."""
  timeout: dt.timedelta = dt.timedelta(minutes=10)
  """The minimum amount of time between replies."""


class RedditBotConfig(BaseSettings):
  client_id: str
  """The client id. See here: https://www.reddit.com/prefs/apps"""
  client_secret: SecretStr
  """The client secret. See here: https://www.reddit.com/prefs/apps"""
  params: RedditBotParams
  """The parameters to use for this bot."""
  password: SecretStr
  """The bot's password."""
  username: str
  """The bot's username."""
  version: str = '0.0.0'
  """The version of this bot."""

  @property
  def user_agent(self) -> str:
    """The unique string for the bot."""
    return f"{self.username}-{self.client_id}-{self.version}"

  class Config: # type: ignore
    env_file = '.env'
    env_file_encoding = 'utf-8'


def get_reddit(config: RedditBotConfig) -> asyncpraw.Reddit:
  """Grabs the reddit instance."""
  return asyncpraw.Reddit(
    client_id=config.client_id,
    client_secret=config.client_secret.get_secret_value(),
    user_agent=config.user_agent,
    username=config.username,
    password=config.password.get_secret_value(),
  )

class RedditBot:
  _config: RedditBotConfig
  _reddit: asyncpraw.Reddit
  _queued_submissions: asyncio.Queue[asyncpraw.reddit.Submission]
  _last_reply_time: dt.datetime

  def __init__(self, config: RedditBotConfig) -> None:
    self._config = config
    self._reddit = get_reddit(config)
    self._queued_submissions = asyncio.Queue()
    self._last_reply_time = dt.datetime.min

  async def _should_reply(self, submission: asyncpraw.reddit.Submission) -> bool:
    text = bs4.BeautifulSoup(submission.body_html).get_text()

    # Ignore comments that don't match the pattern.
    if not self._config.params.pattern.match(text):
      return False

    me: Optional[asyncpraw.reddit.Redditor] = await self._reddit.user.me()
    if not me:
      # TODO: log something here.
      return False

    # Don't respond to myself.
    if submission.author == me:
      return False

    # TODO: optimize this.
    # Don't respond to a submission more than once.
    for comment in me.comments.new():
      # We couldn't have commented before the target submission was created!
      if comment.created_utc < submission.created_utc:
        break

      if comment.parent().id == submission.id:
        return False

    return True

  async def _reply_in_the_future(self, submission: asyncpraw.reddit.Submission) -> None:
    await self._queued_submissions.put(submission)

  async def _start_replying(self) -> None:
    while True:
      submission = await self._queued_submissions.get()

      # How long since the last reply we made?
      time_elapsed_since_last_reply = dt.datetime.now() - self._last_reply_time
      if time_elapsed_since_last_reply < self._config.params.timeout:
        wait_time = self._config.params.timeout - time_elapsed_since_last_reply
        wait_time_in_seconds = math.ceil(wait_time.total_seconds())
        _ = await asyncio.sleep(wait_time_in_seconds)

      _ = await submission.reply(body=self._config.params.reply)
      self._last_reply_time = dt.datetime.now()

  async def stream(self) -> None:
    """Starts streaming."""
    _ = asyncio.create_task(self._start_replying())
    subreddit: asyncpraw.reddit.Submission = await self._reddit.subreddit(self._config.params.subreddit)
    async for submission in subreddit.stream.comments():
      if await self._should_reply(submission):
        await self._reply_in_the_future(submission)
    

async def main() -> None:
  config = RedditBotConfig()
  bot = RedditBot(config)

  await bot.stream()


if __name__ == '__main__':
  loop = asyncio.get_event_loop()
  loop.run_until_complete(main())
