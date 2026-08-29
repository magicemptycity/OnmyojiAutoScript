# This Python file uses the following encoding: utf-8
# @author runhey
# github https://github.com/runhey

import onepush.core
import html
import mimetypes
from email.message import EmailMessage
from pathlib import Path

import yaml
from onepush import get_notifier
from onepush.core import Provider
from onepush.exceptions import OnePushException
from onepush.providers.custom import Custom
from requests import Response
from smtplib import SMTPResponseException

from module.logger import logger
onepush.core.log = logger


class Notifier:
    def __init__(self, _config: str, enable: bool=False) -> None:
        self.config_name: str = ""
        self.enable: bool = enable

        if not self.enable:
            return
        config = {}
        try:
            for item in yaml.safe_load_all(_config):
                config.update(item)
        except Exception as e:
            logger.error("Fail to load onepush config, skip sending")
            return
        self.config = config
        try:
            # 获取provider
            self.provider_name: str = self.config.pop("provider", None)
            if self.provider_name is None:
                logger.info("No provider specified, skip sending")
                return
            # 获取notifier
            self.notifier: Provider = get_notifier(self.provider_name)
            # 获取notifier的必填参数
            self.required: list[str] = self.notifier.params["required"]
        except OnePushException:
            logger.exception("Init notifier failed")
            return
        except Exception as e:
            logger.exception(e)
            return

    def push_image(self, image_path, title='', content='') -> bool:
        """以邮件附件形式发送截图。

        OnePush 的大部分通知提供方只支持文本；当前使用 SMTP 时，通过 MIME 附件直接发送截图。
        """
        if not self.enable:
            return False

        image_file = Path(image_path)
        if not image_file.is_file():
            logger.warning(f'通知图片不存在：{image_file}')
            return self.push(title=title, content=content)
        if self.provider_name.lower() != 'smtp':
            logger.warning('当前通知服务不支持直接发送图片，已改发文字通知')
            return self.push(title=title, content=content)

        safe_title = str(title).replace('\r', '').replace('\n', '').strip()
        message = EmailMessage()
        message['Subject'] = f'{self.config_name} {safe_title}'.strip()
        message['From'] = self.config.get('From') or self.config.get('user')
        message['To'] = self.config.get('To') or self.config.get('user')
        message.set_content(content)

        mime_type, _ = mimetypes.guess_type(image_file.name)
        maintype, subtype = (mime_type or 'image/png').split('/', 1)
        content_id = 'notification-image'
        # 使用正文内嵌图片而不是附件，邮件客户端会直接显示截图。
        message.add_alternative(
            f'<html><body><p>{html.escape(str(content))}</p>'
            f'<img src="cid:{content_id}" alt="截图"></body></html>',
            subtype='html',
        )
        message.get_payload()[-1].add_related(
            image_file.read_bytes(), maintype=maintype, subtype=subtype,
            cid=f'<{content_id}>', filename=image_file.name, disposition='inline',
        )
        try:
            # SMTP 提供方收到 msg 后会直接发送，不再将图片路径当作正文。
            self.notifier.notify(**{**self.config, 'msg': message})
        except SMTPResponseException:
            logger.warning('Appear SMTPResponseException')
            return False
        except OnePushException:
            logger.exception('发送图片通知失败')
            return False
        except Exception as exc:
            logger.exception(exc)
            return False

        logger.info('发送图片通知成功')
        return True

    def push(self, **kwargs) -> bool:
        if not self.enable:
            return False
        # 更新配置
        kwargs["title"] = f"{self.config_name} {kwargs['title']}"
        self.config.update(kwargs)
        # pre check
        for key in self.required:
            if key not in self.config:
                logger.warning(
                    f"Notifier {self.notifier} require param '{key}' but not provided"
                )


        if isinstance(self.notifier, Custom):
            if "method" not in self.config or self.config["method"] == "post":
                self.config["datatype"] = "json"
            if not ("data" in self.config or isinstance(self.config["data"], dict)):
                self.config["data"] = {}
            if "title" in kwargs:
                self.config["data"]["title"] = kwargs["title"]
            if "content" in kwargs:
                self.config["data"]["content"] = kwargs["content"]

        if self.provider_name.lower() == "gocqhttp":
            access_token = self.config.get("access_token")
            if access_token:
                self.config["token"] = access_token


        try:
            resp = self.notifier.notify(**self.config)
            if isinstance(resp, Response):
                if resp.status_code != 200:
                    logger.warning("Push notify failed!")
                    logger.warning(f"HTTP Code:{resp.status_code}")
                    return False
                else:
                    if self.provider_name.lower() == "gocqhttp":
                        return_data: dict = resp.json()
                        if return_data["status"] == "failed":
                            logger.warning("Push notify failed!")
                            logger.warning(
                                f"Return message:{return_data['wording']}")
                            return False
        except SMTPResponseException:
            logger.warning("Appear SMTPResponseException")
            pass
        except OnePushException:
            logger.exception("Push notify failed")
            return False
        except Exception as e:
            logger.exception(e)
            return False

        logger.info("Push notify success")
        return True



