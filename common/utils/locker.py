"""
locker.py
=========
이 파일은 Kairos 프로세스의 중복 실행을 막는 PID 파일 잠금 유틸리티입니다.

실거래 엔진이 두 개 이상 동시에 실행되면 같은 계좌에 중복 진입하거나
보호 주문을 서로 덮어쓸 수 있다. 이 모듈은 시작 시 단일 실행 여부를
확인하고, 정상 종료 시 잠금 파일을 정리하는 최소 안전장치를 제공한다.
"""

import os

from common.utils.logger import logger

class PIDLock:
    """잠금 파일에 현재 PID를 기록해 같은 역할의 프로세스 중복 실행을 방지합니다."""

    def __init__(self, lock_name: str = "trader"):
        """락 이름을 받아 운영체제 임시 폴더에 둘 잠금 파일 경로를 준비합니다."""
        self.lock_file = os.path.join(os.environ.get('TEMP', '/tmp'), f"kairos_{lock_name}.lock")
        self.is_locked = False

    def acquire(self) -> bool:
        """잠금 파일을 원자적으로 생성해 중복 실행 여부를 판단합니다."""
        if os.path.exists(self.lock_file):
            try:
                with open(self.lock_file, 'r') as f:
                    old_pid = f.read().strip()
                    if old_pid:
                        if self._is_process_running(int(old_pid)):
                            logger.error(f"이미 실행 중인 프로세스가 있습니다 (PID: {old_pid}).")
                            return False
                os.remove(self.lock_file)
                pid_text = old_pid if old_pid else "EMPTY"
                logger.warning(f"종료된 프로세스의 stale PID Lock을 정리했습니다. (PID: {pid_text})")
            except Exception as exc:
                logger.error(f"PID Lock 상태 확인 실패: {exc}")
                return False

        try:
            fd = os.open(self.lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, 'w') as f:
                f.write(str(os.getpid()))
            self.is_locked = True
            logger.info(f"PID Lock 획득 (File: {self.lock_file}, PID: {os.getpid()})")
            return True
        except FileExistsError:
            logger.error(f"PID Lock 생성 경합 감지. 이미 실행 중일 수 있습니다. (File: {self.lock_file})")
            return False
        except Exception as e:
            logger.error(f"Lock 파일 생성 실패: {e}")
            return False

    def release(self):
        """현재 프로세스가 획득한 잠금 파일을 삭제해 다음 실행을 허용합니다."""
        if self.is_locked and os.path.exists(self.lock_file):
            try:
                os.remove(self.lock_file)
                self.is_locked = False
                logger.info("PID Lock 해제 완료")
            except Exception as e:
                logger.error(f"Lock 파일 삭제 실패: {e}")

    def _is_process_running(self, pid: int) -> bool:
        """잠금 파일에 남아 있는 PID가 현재도 실행 중인지 운영체제별로 확인합니다."""
        if os.name == 'nt':
            try:
                output = os.popen(f'tasklist /fi "PID eq {pid}"').read()
                return str(pid) in output
            except Exception:
                return True
        else:
            try:
                os.kill(pid, 0)
                return True
            except OSError:
                return False
