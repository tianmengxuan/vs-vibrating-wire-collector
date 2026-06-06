# -*- coding: utf-8 -*-
"""
remote_config/__init__.py - 远程配置模块入口
"""

from .commands import CommandBuilder, send_command

__all__ = ['CommandBuilder', 'send_command']
