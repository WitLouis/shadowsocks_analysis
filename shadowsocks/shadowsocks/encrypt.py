#!/usr/bin/env python
#
# Copyright 2012-2015 clowwindy
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

from __future__ import absolute_import, division, print_function, \
    with_statement

import os
import sys
import hashlib
import logging

from shadowsocks import common
from shadowsocks.crypto import rc4_md5, openssl, sodium, table

# 支持的加密算法
method_supported = {}
method_supported.update(rc4_md5.ciphers)
method_supported.update(openssl.ciphers)
method_supported.update(sodium.ciphers)
method_supported.update(table.ciphers)


def random_string(length):
    """
    随机生成n字节字符串

    :param length: 随机字符串的长度

    :return: n字节字符串
    """
    return os.urandom(length)


cached_keys = {}


def try_cipher(key, method=None):
    """
    尝试加密

    :param key: 用于生成密钥的key

    :param method: 加密算法

    :return: None
    """
    Encryptor(key, method)


def EVP_BytesToKey(password, key_len, iv_len):
    """
    生成key和密钥初始化矢量iv

    :param password: 用于生成key和iv

    :param key_len: key的规定长度

    :param iv_len: iv的规定长度

    :return: 密钥key和密钥初始化向量iv
    """
    # equivalent to OpenSSL's EVP_BytesToKey() with count 1
    # so that we make the same key and iv as nodejs version
    cached_key = '%s-%d-%d' % (password, key_len, iv_len)
    r = cached_keys.get(cached_key, None)
    if r:
        return r
    m = []
    i = 0
    while len(b''.join(m)) < (key_len + iv_len):
        md5 = hashlib.md5()
        data = password
        if i > 0:
            data = m[i - 1] + password
        md5.update(data)
        m.append(md5.digest())
        i += 1
    ms = b''.join(m)
    key = ms[:key_len]
    iv = ms[key_len:key_len + iv_len]
    cached_keys[cached_key] = (key, iv)
    return key, iv


class Encryptor(object):
    """
    加密器,对传输数据进行加密
    """
    def __init__(self, key, method):
        """
        初始化加密器

        :param key: 密钥

        :param method: 加密算法
        """
        self.key = key
        self.method = method
        self.iv = None
        self.iv_sent = False
        self.cipher_iv = b''
        self.decipher = None
        method = method.lower()
        self._method_info = self.get_method_info(method)
        if self._method_info:
            self.cipher = self.get_cipher(key, method, 1,
                                          random_string(self._method_info[1]))
        else:
            logging.error('method %s not supported' % method)
            sys.exit(1)

    def get_method_info(self, method):
        """
        获取加密算法的信息

        :param method: 加密算法

        :return: 加密算法信息m,m[0]key的长度,m[1]iv的长度,m[2]加密算法
        """
        method = method.lower()
        m = method_supported.get(method)
        return m

    def iv_len(self):
        """
        获得iv的长度

        :return: 密钥初始化向量iv长度
        """
        return len(self.cipher_iv)

    def get_cipher(self, password, method, op, iv):
        """
        获得加密算法加密器,m[0]key的长度,m[1]iv的长度,m[2]加密算法

        :param password: 用于生成key和iv

        :param method: 加密算法

        :param op: 操作标识，1是加密，0是解密

        :param iv: key初始化矢量

        :return: 加密器
        """
        password = common.to_bytes(password)
        m = self._method_info
        if m[0] > 0:
            key, iv_ = EVP_BytesToKey(password, m[0], m[1])
        else:
            # key_length == 0 indicates we should use the key directly
            key, iv = password, b''

        iv = iv[:m[1]]
        if op == 1:
            # this iv is for cipher not decipher
            self.cipher_iv = iv[:m[1]]
        return m[2](method, key, iv, op)

    def encrypt(self, buf):
        """
        加密

        :param buf: 需要加密数据

        :return: 密文
        """
        if len(buf) == 0:
            return buf
        if self.iv_sent:
            return self.cipher.update(buf)
        else:
            self.iv_sent = True
            return self.cipher_iv + self.cipher.update(buf)

    def decrypt(self, buf):
        """
        解密

        :param buf: 需要解密数据

        :return: 正文
        """
        if len(buf) == 0:
            return buf
        if self.decipher is None:
            decipher_iv_len = self._method_info[1]
            decipher_iv = buf[:decipher_iv_len]
            self.decipher = self.get_cipher(self.key, self.method, 0,
                                            iv=decipher_iv)
            buf = buf[decipher_iv_len:]
            if len(buf) == 0:
                return buf
        return self.decipher.update(buf)


def encrypt_all(password, method, op, data):
    """
    加密算法加密

    :param password: 密码，用于生成密钥

    :param method: 加密算法

    :param op: 操作标识，op=1加密，op=0解密

    :param data: 加密/解密数据

    :return: 密文
    """
    result = []
    method = method.lower()
    (key_len, iv_len, m) = method_supported[method]
    if key_len > 0:
        key, _ = EVP_BytesToKey(password, key_len, iv_len)
    else:
        key = password
    if op:
        iv = random_string(iv_len)
        result.append(iv)
    else:
        iv = data[:iv_len]
        data = data[iv_len:]
    cipher = m(method, key, iv, op)
    result.append(cipher.update(data))
    return b''.join(result)


CIPHERS_TO_TEST = [
    'aes-128-cfb',
    'aes-256-cfb',
    'rc4-md5',
    'salsa20',
    'chacha20',
    'table',
]


def test_encryptor():
    from os import urandom
    plain = urandom(10240)
    for method in CIPHERS_TO_TEST:
        logging.warn(method)
        encryptor = Encryptor(b'key', method)
        decryptor = Encryptor(b'key', method)
        cipher = encryptor.encrypt(plain)
        plain2 = decryptor.decrypt(cipher)
        assert plain == plain2


def test_encrypt_all():
    from os import urandom
    plain = urandom(10240)
    for method in CIPHERS_TO_TEST:
        logging.warn(method)
        cipher = encrypt_all(b'key', method, 1, plain)
        plain2 = encrypt_all(b'key', method, 0, cipher)
        assert plain == plain2


if __name__ == '__main__':
    test_encrypt_all()
    test_encryptor()
