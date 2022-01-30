#!/usr/bin/env python3
import argparse
import asyncio
import asyncpraw
import bs4
import datetime as dt
import json
import logging
import math
import pathlib
import re
import yaml

from pydantic import BaseModel, BaseSettings, SecretStr
from typing import Optional, Pattern

from functools import cached_property


logger = logging.getLogger(__name__)
FORMAT = "%(asctime)s :: [%(levelname)-8s] :: %(message)s"
logging.basicConfig(format=FORMAT)
logger.setLevel(logging.INFO)

class RedditBotParams(BaseModel):
  dry_run: bool = False
  """If true, no replies will be made."""
  pattern: str
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
  _queued_comments: "asyncio.Queue[asyncpraw.reddit.Comment]"
  _last_reply_time: dt.datetime
  _reply_lock: asyncio.Lock


  @cached_property
  def compiled_pattern(self) -> Pattern[str]:
    return re.compile(self._config.params.pattern, re.IGNORECASE)

  def __init__(self, config: RedditBotConfig) -> None:
    self._config = config
    self._reddit = get_reddit(config)
    self._queued_comments = asyncio.Queue()
    self._last_reply_time = dt.datetime.min
    self._reply_lock = asyncio.Lock()

  async def _should_reply(self, comment: asyncpraw.reddit.Comment) -> bool:
    text = bs4.BeautifulSoup(comment.body_html, features="lxml").get_text()

    # Ignore comments that don't match the pattern.
    if not self.compiled_pattern.search(text):
      logger.debug(f"Skipping reply to {comment.id=} since {text=} does not match pattern {self.compiled_pattern}")
      return False

    me: Optional[asyncpraw.reddit.Redditor] = await self._reddit.user.me()
    if not me:
      logger.critical(f"Skipping reply to {comment.id=} since I could not fetch myself")
      return False

    # Don't respond to myself.
    if comment.author == me:
      logger.info(f"Skipping reply to {comment.id=} since I am the comment author")
      return False

    # TODO: optimize this.
    # Don't respond to a submission more than once.
    async for raw_my_comment in me.comments.new():
      my_comment: asyncpraw.reddit.Comment = raw_my_comment
      # We couldn't have commented before the target submission was created!
      if my_comment.created_utc < comment.created_utc:
        break

      parent: asyncpraw.reddit.Comment = await my_comment.parent()
      if parent.id == comment.id:
        logger.info(f"Skipping reply to {comment.id=} since we already responded ({my_comment.id=})")
        return False

    return True

  async def _reply_in_the_future(self, comment: asyncpraw.reddit.Comment) -> None:
    await self._queued_comments.put(comment)

  async def _start_replying(self) -> None:
    while True:
      comment = await self._queued_comments.get()

      # How long since the last reply we made?
      time_elapsed_since_last_reply = dt.datetime.now() - self._last_reply_time
      if time_elapsed_since_last_reply < self._config.params.timeout:
        wait_time = self._config.params.timeout - time_elapsed_since_last_reply
        wait_time_in_seconds = math.ceil(wait_time.total_seconds())
        logger.info(f"Replier sleeping for {wait_time}")
        _ = await asyncio.sleep(wait_time_in_seconds)

      async with self._reply_lock:
        # Double check that it is okay to reply since time could have passed.
        if not await self._should_reply(comment):
          logger.info(f"double check failed for {comment.id=}")
          continue
          
        _ = asyncio.create_task(self._log_reply(comment))
        if not self._config.params.dry_run:
          _ = await comment.reply(body=self._config.params.reply)
        self._last_reply_time = dt.datetime.now()
  
  @classmethod
  async def format_comment(cls, comment: asyncpraw.reddit.Comment) -> str:
    submission: asyncpraw.reddit.Submission = comment.submission
    await submission.load()
    return json.dumps({
        "title": submission.title,
        "comment": comment.body,
        "id": comment.id,
      },
      indent=2,
      sort_keys=True,
    )

  @classmethod
  async def _log_comment(cls, comment: asyncpraw.reddit.Comment) -> None:
    logger.debug(f"Got comment with content {await cls.format_comment(comment)}")

  @classmethod
  async def _log_reply(cls, comment: asyncpraw.reddit.Comment) -> None:
    logger.info(f"Replying to {await cls.format_comment(comment)}")

  async def _get_subreddit(self) -> asyncpraw.reddit.Submission:
    subreddit: asyncpraw.reddit.Submission

    subreddit_name , _, user_subreddit_name = self._config.params.subreddit.lower().partition('u/')
    if user_subreddit_name:
      redditor: asyncpraw.reddit.Redditor = await self._reddit.redditor(user_subreddit_name)
      await redditor.load()
      subreddit = redditor.subreddit
    else:
      subreddit = await self._reddit.subreddit(subreddit_name)

    return subreddit

  async def stream(self) -> None:
    """Starts streaming."""
    _ = asyncio.create_task(self._start_replying())
    subreddit = await self._get_subreddit()
    async for raw_comment in subreddit.stream.comments():
      comment: asyncpraw.reddit.Comment = raw_comment
      _ = asyncio.create_task(self._log_comment(comment))
      if await self._should_reply(comment):
        _ = asyncio.create_task(self._reply_in_the_future(comment))
    

async def main(args: argparse.Namespace) -> None:
  params_path: pathlib.Path = args.params

  with params_path.open() as params_file:
    params_dict = yaml.load(params_file, yaml.SafeLoader)

  params = RedditBotParams(**params_dict)
  config = RedditBotConfig(params=params)
  bot = RedditBot(config)
  await bot.stream()


def get_args() -> argparse.Namespace:
  parser = argparse.ArgumentParser()
  _ = parser.add_argument("--params", type=pathlib.Path, required=True)
  return parser.parse_args()

if __name__ == '__main__':
  loop = asyncio.get_event_loop()
  loop.run_until_complete(main(get_args()))
