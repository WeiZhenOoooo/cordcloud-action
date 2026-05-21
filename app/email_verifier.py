"""
邮箱验证码获取模块
支持通过IMAP协议从邮箱中自动获取验证码
"""
import imaplib
import email
import re
import time
from html.parser import HTMLParser
from typing import Optional
from email.utils import parsedate_to_datetime

from app import log


class HTMLToTextParser(HTMLParser):
    """HTML转纯文本解析器"""
    
    def __init__(self):
        super().__init__()
        self.text = []
        self.skip_tags = {'script', 'style', 'head'}
        self.in_skip_tag = False
    
    def handle_starttag(self, tag, attrs):
        if tag in self.skip_tags:
            self.in_skip_tag = True
        elif tag == 'br':
            self.text.append('\n')
        elif tag == 'p':
            self.text.append('\n')
    
    def handle_endtag(self, tag):
        if tag in self.skip_tags:
            self.in_skip_tag = False
        elif tag == 'p':
            self.text.append('\n')
    
    def handle_data(self, data):
        if not self.in_skip_tag:
            self.text.append(data)
    
    def get_text(self):
        return ''.join(self.text)


class EmailVerifier:
    """CordCloud邮箱验证码获取器（使用授权码登录）"""
    
    def __init__(self, imap_server: str, email_address: str, auth_code: str, port: int = 993):
        """
        初始化邮箱验证器
        
        Args:
            imap_server: IMAP服务器地址 (如: imap.qq.com, imap.163.com)
            email_address: 邮箱地址
            auth_code: 邮箱授权码（不是登录密码）
            port: IMAP端口，默认993(SSL)
        """
        self.imap_server = imap_server
        self.email_address = email_address
        self.auth_code = auth_code
        self.port = port
        self.mailbox = None
    
    def connect(self):
        """连接到IMAP服务器（使用授权码）"""
        try:
            self.mailbox = imaplib.IMAP4_SSL(self.imap_server, self.port)
            self.mailbox.login(self.email_address, self.auth_code)
            self.mailbox.select('INBOX')
            return True
        except Exception as e:
            raise Exception(f'邮箱连接失败: {str(e)}')
    
    def disconnect(self):
        """断开IMAP连接"""
        if self.mailbox:
            try:
                self.mailbox.close()
                self.mailbox.logout()
            except:
                pass
            self.mailbox = None
    
    @staticmethod
    def _parse_email_date(msg: email.message.Message):
        """
        解析邮件日期
        
        Args:
            msg: 邮件消息对象
            
        Returns:
            datetime对象，如果解析失败则返回None
        """
        
        try:
            date_header = msg.get('Date')
            if date_header:
                mail_date = parsedate_to_datetime(date_header)
                # 转换为本地时间（去除时区信息）
                return mail_date.replace(tzinfo=None)
        except Exception as e:
            log.error(f'解析邮件日期失败: {str(e)}')
        
        return None
    
    def get_verification_code(self, timeout: int = 60, check_interval: int = 5, after_time=None) -> Optional[str]:
        """
        获取最新的验证码
        
        Args:
            timeout: 超时时间(秒),默认60秒
            check_interval: 检查间隔(秒),默认5秒
            after_time: datetime对象,只读取此时间之后的邮件
            
        Returns:
            验证码字符串,如果未找到则返回None
        """
        start_time = time.time()
        
        try:
            if not self.mailbox:
                self.connect()
            
            while time.time() - start_time < timeout:
                # 搜索未读邮件
                status, messages = self.mailbox.search(None, 'UNSEEN')
                
                if status == 'OK' and messages[0]:
                    email_ids = messages[0].split()
                    
                    # 从最新到最旧遍历
                    for email_id in reversed(email_ids):
                        status, msg_data = self.mailbox.fetch(email_id, '(RFC822)')
                        if status != 'OK':
                            continue
                        
                        msg = email.message_from_bytes(msg_data[0][1])
                        
                        # 时间过滤:只处理after_time之后的邮件
                        if after_time:
                            mail_date = self._parse_email_date(msg)
                            if mail_date and mail_date <= after_time:
                                continue  # 跳过旧邮件
                        
                        # 提取验证码
                        body = self._extract_email_body(msg)
                        code = self._extract_code(body)
                        
                        if code:
                            log.info(f'✓ 成功获取验证码: {code}')
                            return code
                
                # 没找到,等待后继续轮询
                time.sleep(check_interval)
            
            log.error(f'⚠️  在{timeout}秒内未找到验证码')
            return None
            
        except Exception as e:
            log.error(f'✗ 获取验证码失败: {str(e)}')
            return None
        finally:
            self.disconnect()
    
    @staticmethod
    def _html_to_text(html: str) -> str:
        """
        将HTML转换为纯文本
        
        Args:
            html: HTML字符串
            
        Returns:
            纯文本字符串
        """
        parser = HTMLToTextParser()
        try:
            parser.feed(html)
            text = parser.get_text()
            # 清理多余空白行
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            return '\n'.join(lines)
        except:
            return html
    
    @staticmethod
    def _extract_email_body(msg: email.message.Message) -> str:
        """
        提取邮件正文（优先HTML，转换为纯文本）
        
        Args:
            msg: 邮件消息对象
            
        Returns:
            邮件正文字符串（纯文本）
        """
        body = ""
        html_body = ""
        
        # 如果是 multipart 邮件
        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                content_disposition = str(part.get("Content-Disposition"))
                
                # 跳过附件
                if "attachment" in content_disposition:
                    continue
                
                # 收集 text/html
                if content_type == "text/html":
                    try:
                        html_body = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                        break  # 找到HTML就停止
                    except:
                        continue
                # 其次收集 text/plain
                elif content_type == "text/plain" and not body:
                    try:
                        body = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                    except:
                        continue
        else:
            # 非 multipart 邮件
            content_type = msg.get_content_type()
            try:
                payload = msg.get_payload(decode=True).decode('utf-8', errors='ignore')
                if content_type == "text/html":
                    html_body = payload
                else:
                    body = payload
            except:
                pass
        
        # 优先使用HTML（转换为纯文本），其次使用plain text
        if html_body:
            return EmailVerifier._html_to_text(html_body)
        return body
    
    @staticmethod
    def _extract_code(text: str) -> Optional[str]:
        """
        从CordCloud验证码邮件中提取验证码
        
        验证逻辑：
        1. 首先确认邮件来自 CordCloud（包含关键词）
        2. 然后提取6位验证码
        
        邮件格式示例：
        CordCloud
        登录验证码
        尊敬的用户：
        我们检测到您正在使用新设备登录账号。
        ...
        您的登录验证码为：
        485766
        ...
        
        Args:
            text: 邮件正文文本
            
        Returns:
            验证码字符串，如果不是CordCloud邮件或未找到则返回None
        """
        if not text:
            return None
        
        # 第一步：验证是否是 CordCloud 的邮件
        # 必须同时包含这些关键词，避免误读其他邮件
        required_keywords = ['CordCloud', '登录验证码']
        for keyword in required_keywords:
            if keyword not in text:
                print(f'⚠️  邮件不包含关键词 "{keyword}"，跳过')
                return None
        
        # 第二步：提取验证码
        # 使用精确模式：要求"您的登录验证码为"后面紧跟6位数字
        patterns = [
            # 最精确：CordCloud + 登录验证码 + 您的登录验证码为
            r'CordCloud.*?登录验证码.*?您的登录验证码为[：:\s]*\n?\s*([0-9]{6})',
            # 次精确：登录验证码 + 您的登录验证码为
            r'登录验证码.*?您的登录验证码为[：:\s]*\n?\s*([0-9]{6})',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE | re.DOTALL)
            if match:
                code = match.group(1)
                # 确保是6位纯数字
                if len(code) == 6 and code.isdigit():
                    log.info(f'✓ 验证通过：邮件来自 CordCloud')
                    return code
        
        log.error('⚠️  未在 CordCloud 邮件中找到6位验证码')
        return None


def get_verification_code_from_email(
    imap_server: str,
    email_address: str,
    auth_code: str,
    timeout: int = 60,
    check_interval: int = 5
) -> Optional[str]:
    """
    便捷函数：从邮箱获取CordCloud验证码（使用授权码）
    
    Args:
        imap_server: IMAP服务器地址
        email_address: 邮箱地址
        auth_code: 邮箱授权码（不是登录密码）
        timeout: 超时时间（秒）
        check_interval: 检查间隔（秒）
        
    Returns:
        验证码字符串，如果未找到则返回None
        
    Examples:
        >>> # QQ邮箱
        >>> code = get_verification_code_from_email(
        ...     'imap.qq.com',
        ...     'user@qq.com',
        ...     'authorization_code'  # QQ邮箱生成的授权码
        ... )
        
        >>> # 163邮箱
        >>> code = get_verification_code_from_email(
        ...     'imap.163.com',
        ...     'user@163.com',
        ...     'authorization_code'  # 163邮箱客户端授权密码
        ... )
    """
    verifier = EmailVerifier(imap_server, email_address, auth_code)
    return verifier.get_verification_code(timeout, check_interval)
