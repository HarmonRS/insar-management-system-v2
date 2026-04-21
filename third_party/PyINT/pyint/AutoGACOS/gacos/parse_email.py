import email
import email.message
import getpass
import imaplib
import poplib
import re
from email.parser import Parser
from email.utils import parseaddr
from pathlib import Path
from typing import Literal, Optional, Union

import pandas as pd
from tqdm.auto import tqdm


class GACOSEmail:
    """a class to retrieve gacos urls from email.

    .. note::
        The IMAP server is used to retrieve content from email. Some email
        service providers may need to enable the IMAP service in the settings.
        **You are recommended to use a new email account to receive gacos urls to
        avoid polluting your own email account**.
    """

    def __init__(
        self,
        username: str,
        password: str,
        host: str,
        prompt: bool = False,
        email_protocol: Literal["imap", "pop3"] = "imap",
        port: Optional[int] = None,
        gacos_email: str = "gacos2017@foxmail.com",
        gacos_suffix: str = "tar.gz",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        date_args: Optional[dict] = None,
        ssl: bool = False,
    ) -> None:
        """Retrieve gacos urls from email.

        Parameters
        ----------
        username : str
            The username of the email address.
        password : str
            The password.
        host : str
            The host of the email address. For example, the host of gmail for
            imap is "imap.gmail.com". You can find the host of your email
            settings or search it on the Internet.
        prompt: bool, optional
            Prompt for username and/or password interactively when they are not
            provided as keyword parameters. Default is False.
        email_protocol : str, one of ["imap", "pop3"], optional
            The protocol of the email. Default is "imap".
        port : int, optional
            The port of the host of your email. If None, the default port will be
            used. Default is None.
        gacos_email : str, optional
            The email address of gacos. Default is "gacos2017@foxmail.com".
        gacos_suffix : str, optional
            The suffix of the gacos file url. Default is "tar.gz". The suffix is used
            to filter urls in the email. This parameter is used to avoid the
            situation that the email contains other urls.
        start_date / end_date: str, optional
            The start/end date of email. Used to filter the email. Default is None. Can be any format that can be parsed by pandas.to_datetime.
        date_args : dict, optional
            The arguments are passed to pandas.to_datetime. Default is None.
        ssl : bool, optional
            Whether to use SSL connection. Default is False.
        """
        if prompt:
            self.username = None
            self.password = None
        else:
            self.username = username
            self.password = password
        self.host = host
        self.email_protocol = email_protocol
        self.port = port
        self.gacos_email = gacos_email
        self.gacos_suffix = gacos_suffix
        self.ssl = ssl

        # date part
        self.start_date = start_date
        self.end_date = end_date
        if date_args is None:
            date_args = {}
        self.date_args = date_args

    def _retrieve_gacos_urls_pop3(self):
        server = login_in_email_pop3(
            self.username, self.password, self.host, self.port, ssl=self.ssl
        )
        print(server.getwelcome())

        nums = server.stat()[0]

        gacos = []
        for i in tqdm(range(1, nums + 1), unit=" emails", desc="Retrieving GACOS Urls"):
            response, msgLines, octets = server.retr(i)
            msgLinesToStr = b"\r\n".join(msgLines).decode("utf8", "ignore")
            messageObject = Parser().parsestr(msgLinesToStr)

            senderContent = messageObject["From"]
            senderRealName, senderAdr = parseaddr(senderContent)
            if senderAdr == self.gacos_email:
                if not in_date_range(
                    pd.to_datetime(messageObject["Date"]).tz_localize(None),
                    self.start_date,
                    self.end_date,
                    self.date_args,
                ):
                    continue

                msgBodyContents = get_content(messageObject)
                info = parse_gacos_info(
                    msgBodyContents,
                    gacos_suffix=self.gacos_suffix,
                )
                if info is not None:
                    gacos.append(info)

        server.quit()

        return gacos

    def _retrieve_gacos_urls_imap(self):
        server = login_in_email_imap(
            self.username, self.password, self.host, self.port, ssl=self.ssl
        )
        if server is None:
            print("IMAP server connection failed, skipping email check.")
            return []
        server.select("inbox")
        status, data = server.search(None, "ALL")

        gacos = []
        for i in tqdm(data[0].split(), unit=" emails", desc="Retrieving GACOS urls"):
            res, msg = server.fetch(i, "(RFC822)")
            for response_part in msg:
                if isinstance(response_part, tuple):
                    msgLines = response_part[1].decode("utf8", "ignore")
                    break

            messageObject = Parser().parsestr(msgLines)

            senderContent = messageObject["From"]
            senderRealName, senderAdr = parseaddr(senderContent)
            if senderAdr == self.gacos_email:
                if not in_date_range(
                    pd.to_datetime(messageObject["Date"]).tz_localize(None),
                    self.start_date,
                    self.end_date,
                    self.date_args,
                ):
                    continue

                msgBodyContents = get_content(messageObject)
                info = parse_gacos_info(
                    msgBodyContents,
                    gacos_suffix=self.gacos_suffix,
                )
                if info is not None:
                    gacos.append(info)

        server.close()

        return gacos

    def retrieve_gacos_urls(
        self,
        output_file: Union[str, Path],
    ):
        """Retrieve gacos urls from username.

        Parameters
        ----------
        output_file : str or Path
            The output file used to save the gacos urls.
        """
        if self.email_protocol == "pop3":
            gacos = self._retrieve_gacos_urls_pop3()
        elif self.email_protocol == "imap":
            gacos = self._retrieve_gacos_urls_imap()
        else:
            raise ValueError("email_protocol must be 'pop3' or 'imap'.")

        cols = ["url", "south", "north", "west", "east", "time", "date"]
        df_gacos = pd.DataFrame(gacos, columns=cols).drop_duplicates(subset="url")

        # save to file
        try:
            df_gacos.to_csv(output_file)
            print(f"Save gacos urls to {output_file}")
        except Exception as e:
            self.df_gacos = df_gacos
            print(e)
            print("Save gacos urls failed")
            print("You can access the gacos urls by `df_gacos` attribute.")


def in_date_range(date, start_date, end_date, date_args={}):
    start_date = pd.to_datetime(start_date, **date_args)
    end_date = pd.to_datetime(end_date, **date_args)
    start_none = start_date is None or pd.isna(start_date)
    end_none = end_date is None or pd.isna(end_date)
    if start_none and end_none:
        return True
    elif start_none:
        return date <= end_date
    elif end_none:
        return date >= start_date
    else:
        return (date >= start_date) and (date <= end_date)


def decodeBody(msgPart: email.message.Message):
    """decode email body

    Parameters
    ----------
    msgPart : email.message.Message
        The email message object.
    """
    contentType = msgPart.get_content_type()
    textContent = ""
    if contentType == "text/plain" or contentType == "text/html":
        content = msgPart.get_payload(decode=True)
        charset = msgPart.get_charset()
        if charset is None:
            contentType = msgPart.get("Content-Type", "").lower()
            position = contentType.find("charset=")
            if position >= 0:
                charset = contentType[position + 8 :].strip()
        if charset:
            textContent = content.decode(charset)
    return textContent


def get_content(messageObject):
    msgBodyContents = []
    if messageObject.is_multipart():  # parse multipart email
        messageParts = messageObject.get_payload()
        for messagePart in messageParts:
            bodyContent = decodeBody(messagePart)
            if bodyContent:
                msgBodyContents.append(bodyContent)
    else:
        bodyContent = decodeBody(messageObject)
        if bodyContent:
            msgBodyContents.append(bodyContent)
    return msgBodyContents


def login_in_email_pop3(username, password, host, port, ssl=False):
    try:
        if username is None:
            username = input("username: ")
        if password is None:
            password = getpass.getpass("password: ")

        if ssl:
            if port is None:
                port = 995
            server = poplib.POP3_SSL(host, port)
        else:
            if port is None:
                port = 110
            server = poplib.POP3(host, port)

        server.user(username)
        server.pass_(password)
        return server
    except Exception as e:
        print(e)
        print("login failed")


def login_in_email_imap(username, password, host, port, ssl=False):
    try:
        if username is None:
            username = input("username: ")
        if password is None:
            password = getpass.getpass("password: ")

        if ssl:
            if port is None:
                port = 993
        else:
            if port is None:
                port = 143

        # 强制 IPv4: monkey-patch getaddrinfo，避免 IPv6 不可达
        import socket as _socket
        _orig_getaddrinfo = _socket.getaddrinfo
        def _ipv4_only_getaddrinfo(*args, **kwargs):
            return _orig_getaddrinfo(args[0], args[1], _socket.AF_INET,
                                      *args[3:], **kwargs)
        _socket.getaddrinfo = _ipv4_only_getaddrinfo
        try:
            if ssl:
                server = imaplib.IMAP4_SSL(host, port)
            else:
                server = imaplib.IMAP4(host, port)
        finally:
            _socket.getaddrinfo = _orig_getaddrinfo

        server.login(username, password)

        # 163/126 等网易邮箱要求登录后发送 ID 命令才能执行 SELECT
        if '163.com' in host or '126.com' in host or 'yeah.net' in host:
            try:
                tag = server._new_tag()
                server.send(tag + b' ID ("name" "pyint" "version" "1.0" '
                            b'"vendor" "pyint")\r\n')
                while True:
                    resp = server.readline()
                    if resp.startswith(tag):
                        break
            except Exception:
                pass

        return server
    except Exception as e:
        print(f"IMAP login failed: {e}")
        return None


def parse_gacos_info(msgBodyContents, gacos_suffix="tar.gz"):
    """Parse gacos info from email body.

    Parameters
    ----------
    msgBodyContents : list
        The email body contents.
    gacos_suffix : str, optional
        The suffix of the gacos file url. Default is "tar.gz". The suffix is used
        to filter urls in the email. This parameter is used to avoid the
        situation that the email contains other urls.
    """

    url, south, north, west, east, _time, date_list = (
        None,
        None,
        None,
        None,
        None,
        None,
        None,
    )
    date_list = []
    for contents in msgBodyContents:
        lines = [i.strip() for i in contents.split("\n") if i]
        for line in lines:
            line = line.strip()
            loc = line.split("=")
            if len(loc) == 2:
                parameter, value = loc
                parameter, value = (parameter.strip(), value.strip())
                if "MinLat" == parameter:
                    south = float(value)
                if "MaxLat" == parameter:
                    north = float(value)
                if "MinLon" == parameter:
                    west = float(value)
                if "MaxLon" == parameter:
                    east = float(value)
            loc = line.split(":")
            if len(loc) == 2:
                parameter, value = loc
                parameter, value = (parameter.strip(), value.strip())
                if "Time" == parameter:
                    _time = float(value)

            if len(line) == 8 and line.isdigit():
                date_list.append(line)

            for i in ["http", "ftp", "https"]:
                result = re.search(f"\({i}.*{gacos_suffix}\)", line)
                if result:
                    url = result.group()[1:-1]
                    break

    if url == south == north == west == east == _time:
        return None
    else:
        return url, south, north, west, east, _time, date_list
