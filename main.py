import asyncio
import praw

from pydantic import BaseSettings, SecretStr

class RedditBotConfig(BaseSettings):
  client_id: str
  client_secret: SecretStr
  username: str
  password: SecretStr
  version: str = '0.0.0'

  @property
  def user_agent(self) -> str:
    return f"{self.username}-{self.client_id}-{self.version}"

  class Config: # type: ignore
    env_file = '.env'
    env_file_encoding = 'utf-8'

async def main() -> None:
  config = RedditBotConfig()
  print(config.user_agent)
  reddit = praw.Reddit(
    client_id=config.client_id,
    client_secret=config.client_secret.get_secret_value(),
    user_agent=config.user_agent,
    username=config.username,
    password=config.password.get_secret_value(),
  )
  print(reddit)

if __name__ == '__main__':
  loop = asyncio.get_event_loop()
  loop.run_until_complete(main())
