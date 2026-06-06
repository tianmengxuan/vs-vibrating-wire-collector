# -*- coding: utf-8 -*-
"""
remote_config/commands.py - 远程指令配置管控
=============================================
功能:
    1. 核心指令封装: @SETM, $GETP, $SETP, $SAVE, $INFO
    2. 指令通过TCP会话下发
    3. 安全管控: 禁止通用地址修改参数
    4. 全流程日志记录

协议规范:
    - 所有下发指令末尾强制追加 "#\\r\\n"
    - 部分设备需先发送 @SETM 进入设置模式
    - 多设备RS485总线场景严禁使用 0xFF 通用地址修改参数
    - 指令最大长度不超过500字符

作者: VS DataCollector Project
版本: 1.0.0
"""

import logging

logger = logging.getLogger(__name__)


# ============================================================================
# 指令常量定义
# ============================================================================

# 默认设备地址（单设备直连场景使用）
DEFAULT_DEVICE_ADDR = 0x01

# 禁止使用的通用广播地址
FORBIDDEN_ADDR = 0xFF

# 指令类型枚举
CMD_TYPES = {
    'SETM': '@SETM',
    'GETP': '$GETP',
    'SETP': '$SETP',
    'SAVE': '$SAVE',
    'INFO': '$INFO',
    'READ': '$READ',
    'RESET': '$RESET',
}

# 指令结束符
CMD_TERMINATOR = '#\r\n'


# ============================================================================
# 指令构建器
# ============================================================================

class CommandBuilder:
    """
    指令构建器。

    封装稳控科技自定义字符集核心指令，自动追加协议规定的结束符 "#\\r\\n"。

    Usage:
        builder = CommandBuilder()
        cmd = builder.enter_set_mode()      # "@SETM#\r\n"
        cmd = builder.read_param(1, 0x10)   # "$GETP 1 16#\r\n"
    """

    # ---- 系统控制指令 ----

    @staticmethod
    def enter_set_mode():
        """
        进入设置模式。

        部分设备需要先发送此指令，才能响应后续的 $GETP/$SETP 读写指令。

        指令格式: @SETM#\\r\\n

        Returns:
            str: 完整指令（含结束符）
        """
        cmd = f"@SETM{CMD_TERMINATOR}"
        logger.debug("构建指令: ENTER_SET_MODE -> %s", cmd.strip())
        return cmd

    @staticmethod
    def exit_set_mode():
        """
        退出设置模式。

        指令格式: @EXIT#\\r\\n

        Returns:
            str: 完整指令（含结束符）
        """
        cmd = f"@EXIT{CMD_TERMINATOR}"
        logger.debug("构建指令: EXIT_SET_MODE -> %s", cmd.strip())
        return cmd

    # ---- 参数读写指令 ----

    @staticmethod
    def read_parameter(device_addr, register):
        """
        读取设备参数。

        指令格式: $GETP <地址> <寄存器>#\\r\\n
        示例: $GETP 1 16 → 读取地址1设备的寄存器16

        设备地址说明:
            - 单设备直连: 通常为 1
            - RS485总线: 对应设备的总线地址（1-254）
            - 禁止使用 0xFF（通用地址），只能用于读取，严禁写入

        Args:
            device_addr (int): 设备RS485地址（1-254）
            register (int): 寄存器编号

        Returns:
            str: 完整指令（含结束符）

        Raises:
            ValueError: 设备地址为0xFF时抛出（读取允许，但会警告）
        """
        if device_addr == FORBIDDEN_ADDR:
            logger.warning("使用通用地址0xFF读取参数，仅允许读取操作")

        if not (1 <= device_addr <= 254):
            raise ValueError(f"设备地址无效: {device_addr}，有效范围1-254")

        cmd = f"$GETP {device_addr} {register}{CMD_TERMINATOR}"
        logger.debug("构建指令: READ_PARAM -> %s", cmd.strip())
        return cmd

    @staticmethod
    def write_parameter(device_addr, register, value):
        """
        写入设备参数。

        指令格式: $SETP <地址> <寄存器> <值>#\\r\\n
        示例: $SETP 1 16 100 → 将地址1设备的寄存器16设置为100

        安全规则:
            **严禁使用 0xFF 通用地址写入参数！**
            多设备RS485总线场景下，使用0xFF会导致所有设备同时修改参数。

        Args:
            device_addr (int): 设备RS485地址（1-254）
            register (int): 寄存器编号
            value: 参数值（int/float/str）

        Returns:
            str: 完整指令（含结束符）

        Raises:
            ValueError: 设备地址为0xFF时抛出（写入禁止）
        """
        # 安全管控：禁止通用广播地址写入
        if device_addr == FORBIDDEN_ADDR:
            error_msg = "禁止使用通用地址(0xFF)写入参数！多设备RS485场景下会导致所有设备同时修改。"
            logger.critical(error_msg)
            raise ValueError(error_msg)

        if not (1 <= device_addr <= 254):
            raise ValueError(f"设备地址无效: {device_addr}，有效范围1-254")

        cmd = f"$SETP {device_addr} {register} {value}{CMD_TERMINATOR}"
        logger.info("构建指令: WRITE_PARAM [ADDR=%d REG=%d VAL=%s]", device_addr, register, value)
        return cmd

    # ---- 保存指令 ----

    @staticmethod
    def save_parameters():
        """
        保存参数到设备。

        将当前设置的参数保存到设备非易失性存储器，
        确保设备断电后参数不丢失。

        指令格式: $SAVE#\\r\\n

        Returns:
            str: 完整指令（含结束符）
        """
        cmd = f"$SAVE{CMD_TERMINATOR}"
        logger.debug("构建指令: SAVE_PARAMS -> %s", cmd.strip())
        return cmd

    # ---- 设备信息指令 ----

    @staticmethod
    def get_device_info(device_addr=DEFAULT_DEVICE_ADDR):
        """
        获取设备信息。

        指令格式: $INFO <地址>#\\r\\n
        返回: 设备型号、固件版本、序列号等。

        Args:
            device_addr (int): 设备地址，默认1

        Returns:
            str: 完整指令（含结束符）
        """
        cmd = f"$INFO {device_addr}{CMD_TERMINATOR}"
        logger.debug("构建指令: GET_INFO [ADDR=%d] -> %s", device_addr, cmd.strip())
        return cmd

    # ---- 批量配置指令生成 ----

    @staticmethod
    def generate_config_sequence(device_addr, param_dict):
        """
        生成批量参数配置的指令序列。

        流程:
            1. @SETM（进入设置模式）
            2. $SETP addr reg value × N（逐条写入参数）
            3. $SAVE（保存参数）
            4. @EXIT（退出设置模式）

        安全规则:
            禁止使用 0xFF 广播地址。

        Args:
            device_addr (int): 设备RS485地址
            param_dict (dict): {register: value} 参数字典

        Returns:
            list[str]: 完整指令序列

        Raises:
            ValueError: 设备地址为0xFF时抛出
        """
        if device_addr == FORBIDDEN_ADDR:
            raise ValueError("禁止使用通用地址(0xFF)进行参数配置！")

        commands = []

        # 步骤1: 进入设置模式
        commands.append(CommandBuilder.enter_set_mode())

        # 步骤2: 逐条写入参数
        for register, value in param_dict.items():
            commands.append(
                CommandBuilder.write_parameter(device_addr, register, value)
            )

        # 步骤3: 保存参数
        commands.append(CommandBuilder.save_parameters())

        # 步骤4: 退出设置模式
        commands.append(CommandBuilder.exit_set_mode())

        logger.info("生成批量配置指令序列: %d条 (ADDR=%d)", len(commands), device_addr)
        return commands

    # ---- 指令日志记录 ----

    @staticmethod
    def log_command_to_db(db_manager, udid, command_type, command_content,
                          command_full, status='PENDING', reply_content=None,
                          error_message=None, retry_count=0):
        """
        将指令记录写入数据库。

        Args:
            db_manager: DatabaseManager实例
            udid (str): 设备UDID
            command_type (str): 指令类型
            command_content (str): 指令内容（不含结束符）
            command_full (str): 完整指令（含结束符）
            status (str): 执行状态
            reply_content (str): 应答内容
            error_message (str): 错误信息
            retry_count (int): 重试次数

        Returns:
            int or None: 日志记录ID
        """
        if not db_manager or not db_manager.is_connected():
            logger.warning("数据库未连接，指令日志仅写入本地日志")
            return None

        log_id = db_manager.log_command(
            udid=udid,
            command_type=command_type,
            command_content=command_content,
            command_full=command_full,
            status=status,
            reply_content=reply_content,
            reply_received=bool(reply_content),
            error_message=error_message,
            retry_count=retry_count,
        )
        return log_id


# ============================================================================
# 便捷发送函数
# ============================================================================

async def send_command(device_session, command, udid='', db_manager=None,
                       command_type='OTHER', timeout=15, max_retries=3,
                       auto_log=True):
    """
    通过设备会话发送指令的便捷异步函数。

    处理流程:
        1. 构建完整指令（自动追加 #\\r\\n）
        2. 通过TCP会话发送
        3. 等待设备应答（超时重试）
        4. 记录日志

    注意: 此函数需要在 asyncio 事件循环中调用。

    Args:
        device_session: DeviceSession实例（需要有 send_and_wait_reply 方法）
        command (str): 指令内容（已含结束符）
        udid (str): 设备UDID
        db_manager: 数据库管理器
        command_type (str): 指令类型
        timeout (int): 超时时间(秒)
        max_retries (int): 最大重试次数
        auto_log (bool): 是否自动写入数据库

    Returns:
        tuple: (success, reply)
    """
    # 确保指令以正确的结束符结尾
    if not command.endswith(CMD_TERMINATOR):
        if command.endswith('\r\n'):
            command = command[:-2] + CMD_TERMINATOR
        elif command.endswith('#'):
            command = command + '\r\n'
        else:
            command = command + CMD_TERMINATOR

    logger.info("发送指令: UDID=%s | Type=%s | CMD=%s", udid, command_type, command.strip())

    # 记录日志
    log_id = None
    if auto_log and db_manager:
        log_id = CommandBuilder.log_command_to_db(
            db_manager=db_manager,
            udid=udid,
            command_type=command_type,
            command_content=command.strip(),
            command_full=command,
            status='SENT',
        )

    # 发送并等待应答
    success = False
    reply = None
    error_msg = None

    for attempt in range(1, max_retries + 1):
        try:
            # 发送指令
            await device_session.send_data(command.encode('ascii'))

            # 等待应答
            reply = await device_session.wait_for_reply(timeout=timeout)

            if reply:
                success = True
                logger.info("指令应答: UDID=%s | 第%d次 | 应答=%s",
                          udid, attempt, reply.strip())
                break
            else:
                error_msg = f"第{attempt}次超时无应答"
                logger.warning("指令无应答: UDID=%s | %s", udid, error_msg)

        except Exception as e:
            error_msg = f"第{attempt}次异常: {str(e)}"
            logger.error("指令发送异常: UDID=%s | %s", udid, error_msg)

    # 更新日志
    if auto_log and db_manager and log_id:
        db_manager.update_command_log(
            log_id=log_id,
            status='SUCCESS' if success else 'FAILED',
            reply_content=reply,
            reply_received=success,
            error_message=error_msg if not success else None,
            retry_count=attempt - 1,
        )

    return success, reply


# ============================================================================
# 模块自检
# ============================================================================

if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG,
                       format='%(asctime)s | %(levelname)s | %(message)s')

    print("=" * 60)
    print("remote_config/commands.py 模块自检")
    print("=" * 60)

    builder = CommandBuilder()

    # 测试各指令构建
    print("\n[1] 核心指令构建:")

    cmd = builder.enter_set_mode()
    print(f"  进入设置模式: {repr(cmd)}")

    cmd = builder.read_parameter(1, 16)
    print(f"  读参数:       {repr(cmd)}")

    cmd = builder.write_parameter(1, 16, 100)
    print(f"  写参数:       {repr(cmd)}")

    cmd = builder.save_parameters()
    print(f"  保存参数:     {repr(cmd)}")

    cmd = builder.get_device_info(1)
    print(f"  设备信息:     {repr(cmd)}")

    # 测试安全管控
    print("\n[2] 安全管控测试:")

    try:
        builder.write_parameter(0xFF, 16, 100)
    except ValueError as e:
        print(f"  写入0xFF: 被拦截 → {e}")

    # 警告但不阻止读取
    try:
        cmd = builder.read_parameter(0xFF, 16)
        print(f"  读取0xFF: 允许但警告 → {repr(cmd)}")
    except ValueError as e:
        print(f"  读取0xFF: 也被拦截 → {e}")

    # 测试批量配置序列
    print("\n[3] 批量配置序列:")
    config_params = {16: 100, 17: 200, 18: 300}
    sequence = builder.generate_config_sequence(1, config_params)
    for i, cmd in enumerate(sequence, 1):
        print(f"  [{i}] {repr(cmd)}")

    # 测试指令结束符
    print("\n[4] 结束符检查:")
    test_commands = [
        "@SETM",
        "$GETP 1 16",
        "$SETP 1 16 100",
    ]
    for cmd in test_commands:
        full_cmd = cmd + CMD_TERMINATOR
        assert full_cmd.endswith("#\r\n"), f"指令 {cmd} 结束符不正确"
        print(f"  {cmd} → {repr(full_cmd)}")

    print("\n自检完成!")
