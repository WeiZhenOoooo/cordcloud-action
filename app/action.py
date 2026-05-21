import re
import time
import datetime
from typing import Tuple, Optional

from cloakbrowser import launch
from app.email_verifier import EmailVerifier
from app import log


class Action:
    # 常量定义
    LOGIN_PATH = 'auth/login'
    USER_PATH = 'user'
    CHECKIN_PATH = 'user/checkin'

    def __init__(self, email: str, passwd: str, code: str = '',
                 host: str = 'cordcloud.us', imap_server: str = '',
                 auth_code: str = ''):
        self.email = email
        self.passwd = passwd
        self.code = code
        self.imap_server = imap_server
        self.auth_code = auth_code
        self.host = self._clean_host(host)
        self.browser = None
        self.page = None
        self.context = None

    @staticmethod
    def _clean_host(host: str) -> str:
        """清理host字符串，移除协议前缀和空白字符"""
        return host.replace('https://', '').replace('http://', '').strip()

    def format_url(self, path: str) -> str:
        """格式化完整URL"""
        return f'https://{self.host}/{path}'

    def _is_logged_in(self) -> bool:
        """检查是否已登录"""
        return self.USER_PATH in self.page.url

    def _fill_login_form(self):
        """填写登录表单"""
        self.page.fill('input[name="Email"]', self.email, timeout=10000)
        self.page.fill('input[name="Password"]', self.passwd, timeout=10000)

        if self.code:
            try:
                self.page.fill('input[name="code"]', self.code, timeout=5000)
            except Exception:
                pass  # 验证码字段可能不存在

    def _handle_verification(self) -> bool:
        """处理设备验证（邮箱验证码）"""
        log.info('网站要求设备验证，已发送验证码到您的邮箱')
        time.sleep(5)
        if self.imap_server:
            # 记录当前时间点，用于后续过滤邮件
            verification_request_time = datetime.datetime.now()
            log.info(f'⏰ 验证请求时间: {verification_request_time}')
            # 减去2秒作为缓冲，避免邮件时间略早于请求时间而被过滤
            verification_request_time = verification_request_time - datetime.timedelta(seconds=10)
            log.info(f'⏰ 验证请求时间(含缓冲): {verification_request_time}')
            
            # 等待邮件到达
            log.info('⏳ 等待邮件到达...')
            
            log.info('正在使用IMAP服务器获取验证码...')
            email_verifier = EmailVerifier(self.imap_server, self.email, self.auth_code)
            
            # 传入验证请求时间，确保只读取该时间点之后的邮件
            verification_code = email_verifier.get_verification_code(
                timeout=60,
                check_interval=3,
                after_time=verification_request_time
            )
            
            if verification_code:
                log.info(f'✓ 填写验证码: {verification_code}')
                self.page.fill('input[name="code"]', verification_code, timeout=10000)
                time.sleep(0.5)
                # 点击提交按钮
                submit_btn = self.page.query_selector(
                    'button[type="submit"], button:has-text("提交"), button:has-text("验证")'
                )
                if submit_btn:
                    submit_btn.click()
                    log.info('已提交验证码')
                    return True
                else:
                    log.warning('未找到验证码提交按钮')
                    return False
            else:
                log.error('✗ 未能获取验证码')
                return False
        
        return True

    def _check_login_result(self) -> dict:
        """检测登录结果弹窗
            
        Returns:
            dict: {
                'success': bool,  # 是否成功
                'needs_verification': bool,  # 是否需要邮箱验证
                'message': str  # 提示信息
            }
        """
        try:
            # 等待弹窗出现并变为可见（最多5秒）
            for _ in range(10):
                time.sleep(0.5)
                modal = self.page.query_selector('#result')
                if modal and modal.is_visible():
                    break
            else:
                # 超时未出现弹窗，可能直接跳转了
                return {'success': True, 'needs_verification': False, 'message': ''}
                
            # 获取弹窗消息
            msg_element = self.page.query_selector('#msg')
            message = msg_element.text_content().strip() if msg_element else ''
                
            log.info(f'登录结果弹窗: {message}')
                
            # 判断是否需要邮箱验证
            verification_keywords = ['检测到陌生设备', '需要进行二步验证']
            needs_verification = any(keyword in message.lower() for keyword in verification_keywords)
                
            if needs_verification:
                return {'success': False, 'needs_verification': True, 'message': message}
                
            # 判断是否失败
            error_keywords = ['失败', '错误', 'error', 'failed', '密码', 'password']
            is_error = any(keyword in message.lower() for keyword in error_keywords)
                
            if is_error:
                # 点击知道了按钮关闭弹窗
                ok_btn = self.page.query_selector('#result_ok')
                if ok_btn:
                    try:
                        ok_btn.click()
                    except Exception:
                        pass  # 页面可能正在跳转
                return {'success': False, 'needs_verification': False, 'message': message}
            
            # 其他情况视为成功提示（如"欢迎回来"、"验证成功"等）
            # 不点击关闭按钮，页面会自动跳转
            return {'success': True, 'needs_verification': False, 'message': message}
                
        except Exception as e:
            log.warning(f'检测登录结果时出错: {str(e)}')
            return {'success': True, 'needs_verification': False, 'message': ''}

    def _wait_for_login_success(self, timeout: int = 15000):
        """等待登录成功跳转"""
        try:
            self.page.wait_for_url(f'**/{self.USER_PATH}*', timeout=timeout)
        except Exception:
            current_url = self.page.url
            log.info(f'当前URL: {current_url}')
            if self.USER_PATH not in current_url:
                raise Exception(f'登录超时，当前URL: {current_url}，请检查账号密码是否正确')
            log.info('已成功跳转到用户页面')

    def _wait_for_altcha_verification(self, timeout: int = 30000) -> bool:
        """等待ALTCHA验证码完成
        
        Returns:
            dict: {'success': bool, 'message': str}
        """
        
        start_time = time.time()
        log.info('等待ALTCHA验证码...')
        
        while time.time() - start_time < timeout / 1000:
            try:
                # 获取altcha widget元素
                altcha_widget = self.page.query_selector('#altcha-widget')
                if not altcha_widget:
                    # 没有验证码组件,直接返回成功
                    log.info('未检测到ALTCHA验证码组件')
                    return False
                
                # 获取当前状态
                altcha_div = altcha_widget.query_selector('.altcha')
                if altcha_div:
                    state = altcha_div.get_attribute('data-state')
                    log.info(f'ALTCHA状态: {state}')
                    
                    # 检查是否验证成功
                    if state == 'verified':
                        log.info('ALTCHA验证成功!')
                        return True
                    
                    # 检查是否验证失败
                    elif state == 'error' or state == 'expired':
                        error_msg = altcha_widget.text_content()
                        log.warning(f'ALTCHA验证失败: {error_msg}')
                        return False
                
                # 短暂等待后继续检查
                time.sleep(0.5)
                
            except Exception as e:
                log.warning(f'检查ALTCHA状态时出错: {str(e)}')
                time.sleep(0.5)
        
        # 超时
        log.warning('ALTCHA验证超时')
        return False

    def login(self) -> dict:
        """执行登录流程"""
        try:
            # 启动浏览器
            self.browser = launch(args=['--no-sandbox'], headless=True)

            # 创建新上下文
            self.context = self.browser.new_context()
            self.page = self.context.new_page()

            # 访问登录页
            login_url = self.format_url(self.LOGIN_PATH)
            self.page.goto(login_url, wait_until='domcontentloaded', timeout=30000)

            # 检查是否已经登录
            if self._is_logged_in():
                log.info('检测到已登录状态，跳过登录步骤')
                return {'ret': 1, 'msg': '已登录'}

            # 执行登录
            log.info('开始登录...')
            self._fill_login_form()

            if self._wait_for_altcha_verification():
                self.page.click('button[type="submit"]', timeout=10000)

                # 检测弹窗结果
                result = self._check_login_result()
                
                if result['needs_verification']:
                    # 需要邮箱验证
                    self._handle_verification()
                elif not result['success']:
                    # 登录失败
                    return {'ret': 0, 'msg': result['message']}
                
                # 等待登录成功跳转
                self._wait_for_login_success(timeout=30000)
                return {'ret': 1, 'msg': '登录成功'}
            else:
                return {'ret': 0, 'msg': 'ALTCHA验证失败'}
        except Exception as e:
            error_msg = str(e)
            log.warning(f'登录失败: {error_msg}')
            return {'ret': 0, 'msg': error_msg}

    def check_in(self) -> dict:
        """执行签到"""
        check_in_url = self.format_url(self.CHECKIN_PATH)
        response = self.page.request.post(check_in_url)
        return response.json()

    def info(self) -> Tuple[str, ...]:
        """获取用户流量信息"""
        # 导航到用户页面（如果不在）
        if not self._is_logged_in():
            user_url = self.format_url(self.USER_PATH)
            self.page.goto(user_url, timeout=30000)

        html = self.page.content()

        # 提取流量信息
        traffic_data = self._parse_traffic_info(html)

        if traffic_data:
            return traffic_data

        return ()

    @staticmethod
    def _parse_traffic_info(html: str) -> Optional[Tuple[str, str, str]]:
        """从HTML中解析流量信息"""
        patterns = {
            'today': r'<span class="user-traffic-label">今日已用</span>\s*<span class="user-badge warning">(.*?)</span>',
            'total': r'<span class="user-traffic-label">过去已用</span>\s*<span class="user-badge [^"]+">(.*?)</span>',
            'rest': r'<span class="user-traffic-label">剩余流量</span>\s*<span class="user-badge success"[^>]*>(.*?)</span>',
        }

        matches = {}
        for key, pattern in patterns.items():
            match = re.search(pattern, html, re.S)
            if match:
                matches[key] = match.group(1)

        if len(matches) == 3:
            return matches['today'], matches['total'], matches['rest']

        return None

    def run(self):
        """执行完整流程：登录 -> 签到 -> 获取信息"""
        try:
            self.login()
            self.check_in()
            self.info()
        finally:
            self.close()

    def close(self):
        """关闭浏览器资源"""
        if self.browser:
            self.browser.close()
            self.browser = None
            self.context = None
            self.page = None
