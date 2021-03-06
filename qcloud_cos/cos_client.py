# -*- coding=utf-8

import requests
import urllib
import logging
import hashlib
import base64
import os
import sys
import copy
import xml.dom.minidom
import xml.etree.ElementTree
from requests import Request, Session
from urllib import quote
from streambody import StreamBody
from xml2dict import Xml2Dict
from dicttoxml import dicttoxml
from cos_auth import CosS3Auth
from cos_exception import CosClientError
from cos_exception import CosServiceError

logging.basicConfig(
                level=logging.INFO,
                format='%(asctime)s %(filename)s[line:%(lineno)d] %(levelname)s %(message)s',
                datefmt='%a, %d %b %Y %H:%M:%S',
                filename='cos_v5.log',
                filemode='w')
logger = logging.getLogger(__name__)
reload(sys)
sys.setdefaultencoding('utf-8')

# kwargs中params到http headers的映射
maplist = {
            'ContentLength': 'Content-Length',
            'ContentMD5': 'Content-MD5',
            'ContentType': 'Content-Type',
            'CacheControl': 'Cache-Control',
            'ContentDisposition': 'Content-Disposition',
            'ContentEncoding': 'Content-Encoding',
            'ContentLanguage': 'Content-Language',
            'Expires': 'Expires',
            'ResponseContentType': 'response-content-type',
            'ResponseContentLanguage': 'response-content-language',
            'ResponseExpires': 'response-expires',
            'ResponseCacheControl': 'response-cache-control',
            'ResponseContentDisposition': 'response-content-disposition',
            'ResponseContentEncoding': 'response-content-encoding',
            'Metadata': 'Metadata',
            'ACL': 'x-cos-acl',
            'GrantFullControl': 'x-cos-grant-full-control',
            'GrantWrite': 'x-cos-grant-write',
            'GrantRead': 'x-cos-grant-read',
            'StorageClass': 'x-cos-storage-class',
            'Range': 'Range',
            'IfMatch': 'If-Match',
            'IfNoneMatch': 'If-None-Match',
            'IfModifiedSince': 'If-Modified-Since',
            'IfUnmodifiedSince': 'If-Unmodified-Since',
            'CopySourceIfMatch': 'x-cos-copy-source-If-Match',
            'CopySourceIfNoneMatch': 'x-cos-copy-source-If-None-Match',
            'CopySourceIfModifiedSince': 'x-cos-copy-source-If-Modified-Since',
            'CopySourceIfUnmodifiedSince': 'x-cos-copy-source-If-Unmodified-Since',
            'VersionId': 'x-cos-version-id',
           }


def to_unicode(s):
    if isinstance(s, unicode):
        return s
    else:
        return s.decode('utf-8')


def get_md5(data):
    m2 = hashlib.md5(data)
    MD5 = base64.standard_b64encode(m2.digest())
    return MD5


def dict_to_xml(data):
    """V5使用xml格式，将输入的dict转换为xml"""
    doc = xml.dom.minidom.Document()
    root = doc.createElement('CompleteMultipartUpload')
    doc.appendChild(root)

    if 'Part' not in data.keys():
        raise CosClientError("Invalid Parameter, Part Is Required!")

    for i in data['Part']:
        nodePart = doc.createElement('Part')

        if 'PartNumber' not in i.keys():
            raise CosClientError("Invalid Parameter, PartNumber Is Required!")

        nodeNumber = doc.createElement('PartNumber')
        nodeNumber.appendChild(doc.createTextNode(str(i['PartNumber'])))

        if 'ETag' not in i.keys():
            raise CosClientError("Invalid Parameter, ETag Is Required!")

        nodeETag = doc.createElement('ETag')
        nodeETag.appendChild(doc.createTextNode(str(i['ETag'])))

        nodePart.appendChild(nodeNumber)
        nodePart.appendChild(nodeETag)
        root.appendChild(nodePart)
    return doc.toxml('utf-8')


def xml_to_dict(data, origin_str="", replace_str=""):
    """V5使用xml格式，将response中的xml转换为dict"""
    root = xml.etree.ElementTree.fromstring(data)
    xmldict = Xml2Dict(root)
    xmlstr = str(xmldict)
    xmlstr = xmlstr.replace("{http://www.qcloud.com/document/product/436/7751}", "")
    xmlstr = xmlstr.replace("{http://www.w3.org/2001/XMLSchema-instance}", "")
    if origin_str:
        xmlstr = xmlstr.replace(origin_str, replace_str)
    xmldict = eval(xmlstr)
    return xmldict


def get_id_from_xml(data, name):
    """解析xml中的特定字段"""
    tree = xml.dom.minidom.parseString(data)
    root = tree.documentElement
    result = root.getElementsByTagName(name)
    # use childNodes to get a list, if has no child get itself
    return result[0].childNodes[0].nodeValue


def mapped(headers):
    """S3到COS参数的一个映射"""
    _headers = dict()
    for i in headers.keys():
        if i in maplist:
            _headers[maplist[i]] = headers[i]
        else:
            raise CosClientError('No Parameter Named '+i+' Please Check It')
    return _headers


def format_xml(data, root, lst=list()):
    """将dict转换为xml"""
    xml_config = dicttoxml(data, item_func=lambda x: x, custom_root=root, attr_type=False)
    for i in lst:
        xml_config = xml_config.replace(i+i, i)
    return xml_config


def format_region(region):
    """格式化地域"""
    if region.find('cos.') != -1:
        return region  # 传入cos.ap-beijing-1这样显示加上cos.的region
    if region == 'cn-north' or region == 'cn-south' or region == 'cn-east' or region == 'cn-south-2' or region == 'cn-southwest' or region == 'sg':
        return region  # 老域名不能加cos.
    #  支持v4域名映射到v5
    if region == 'cossh':
        return 'cos.ap-shanghai'
    if region == 'cosgz':
        return 'cos.ap-guangzhou'
    if region == 'cosbj':
        return 'cos.ap-beijing'
    if region == 'costj':
        return 'cos.ap-beijing-1'
    if region == 'coscd':
        return 'cos.ap-chengdu'
    if region == 'cossgp':
        return 'cos.ap-singapore'
    if region == 'coshk':
        return 'cos.ap-hongkong'
    if region == 'cosca':
        return 'cos.na-toronto'
    if region == 'cosger':
        return 'cos.eu-frankfurt'

    return 'cos.' + region  # 新域名加上cos.


class CosConfig(object):
    """config类，保存用户相关信息"""
    def __init__(self, Appid, Region, Access_id, Access_key, Token=None):
        """初始化，保存用户的信息

        :param Appid(string): 用户APPID.
        :param Region(string): 地域信息.
        :param Access_id(string): 秘钥SecretId.
        :param Access_key(string): 秘钥SecretKey.
        :param Token(string): 临时秘钥使用的token.
        """
        self._appid = Appid
        self._region = format_region(Region)
        self._access_id = Access_id
        self._access_key = Access_key
        self._token = Token
        logger.info("config parameter-> appid: {appid}, region: {region}".format(
                 appid=Appid,
                 region=Region))

    def uri(self, bucket, path=None):
        """拼接url

        :param bucket(string): 存储桶名称.
        :param path(string): 请求COS的路径.
        :return(string): 请求COS的URL地址.
        """
        if path:
            if path[0] == '/':
                path = path[1:]
            url = u"http://{bucket}-{uid}.{region}.myqcloud.com/{path}".format(
                bucket=to_unicode(bucket),
                uid=self._appid,
                region=self._region,
                path=to_unicode(path)
            )
        else:
            url = u"http://{bucket}-{uid}.{region}.myqcloud.com/".format(
                bucket=to_unicode(bucket),
                uid=self._appid,
                region=self._region
            )
        return url


class CosS3Client(object):
    """cos客户端类，封装相应请求"""
    def __init__(self, conf, retry=1, session=None):
        """初始化client对象

        :param conf(CosConfig): 用户的配置.
        :param retry(int): 失败重试的次数.
        :param session(object): http session.
        """
        self._conf = conf
        self._retry = retry  # 重试的次数，分片上传时可适当增大
        if session is None:
            self._session = requests.session()
        else:
            self._session = session

    def get_auth(self, Method, Bucket, Key='', Expired=300, headers={}, params={}):
        """获取签名

        :param Method(string): http method,如'PUT','GET'.
        :param Bucket(string): 存储桶名称.
        :param Key(string): 请求COS的路径.
        :param Expired(int): 签名有效时间,单位为s.
        :param headers(dict): 签名中的http headers.
        :param params(dict): 签名中的http params.
        :return (string): 计算出的V5签名.
        """
        url = self._conf.uri(bucket=Bucket, path=quote(Key, '/-_.~'))
        r = Request(Method, url, headers=headers, params=params)
        auth = CosS3Auth(self._conf._access_id, self._conf._access_key, Key, params, Expired)
        return auth(r).headers['Authorization']

    def send_request(self, method, url, timeout=30, **kwargs):
        """封装request库发起http请求"""
        if self._conf._token is not None:
            kwargs['headers']['x-cos-security-token'] = self._conf._token
        kwargs['headers']['User-Agent'] = 'cos-python-sdk-v5'
        try:
            for j in range(self._retry):
                if method == 'POST':
                    res = self._session.post(url, timeout=timeout, **kwargs)
                elif method == 'GET':
                    res = self._session.get(url, timeout=timeout, **kwargs)
                elif method == 'PUT':
                    res = self._session.put(url, timeout=timeout, **kwargs)
                elif method == 'DELETE':
                    res = self._session.delete(url, timeout=timeout, **kwargs)
                elif method == 'HEAD':
                    res = self._session.head(url, timeout=timeout, **kwargs)
                if res.status_code < 300:
                    return res
        except Exception as e:  # 捕获requests抛出的如timeout等客户端错误,转化为客户端错误
            logger.exception('url:%s, exception:%s' % (url, str(e)))
            raise CosClientError(str(e))

        if res.status_code >= 400:  # 所有的4XX,5XX都认为是COSServiceError
            if method == 'HEAD' and res.status_code == 404:   # Head 需要处理
                info = dict()
                info['code'] = 'NoSuchResource'
                info['message'] = 'The Resource You Head Not Exist'
                info['resource'] = url
                info['requestid'] = res.headers['x-cos-request-id']
                info['traceid'] = res.headers['x-cos-trace-id']
                logger.error(info)
                raise CosServiceError(method, info, res.status_code)
            else:
                msg = res.text
                if msg == '':  # 服务器没有返回Error Body时 给出头部的信息
                    msg = res.headers
                logger.error(msg)
                raise CosServiceError(method, msg, res.status_code)

    #  s3 object interface begin
    def put_object(self, Bucket, Body, Key, **kwargs):
        """单文件上传接口，适用于小文件，最大不得超过5GB

        :param Bucket(string): 存储桶名称.
        :param Body(file|string): 上传的文件内容，类型为文件流或字节流.
        :param Key(string): COS路径.
        :kwargs(dict): 设置上传的headers.
        :return(dict): 上传成功返回的结果，包含ETag等信息.
        """
        headers = mapped(kwargs)
        if 'Metadata' in headers.keys():
            for i in headers['Metadata'].keys():
                headers[i] = headers['Metadata'][i]
            headers.pop('Metadata')

        url = self._conf.uri(bucket=Bucket, path=quote(Key, '/-_.~'))  # 提前对key做encode
        logger.info("put object, url=:{url} ,headers=:{headers}".format(
            url=url,
            headers=headers))
        rt = self.send_request(
            method='PUT',
            url=url,
            auth=CosS3Auth(self._conf._access_id, self._conf._access_key, Key),
            data=Body,
            headers=headers)

        response = rt.headers
        return response

    def get_object(self, Bucket, Key, **kwargs):
        """单文件下载接口

        :param Bucket(string): 存储桶名称.
        :param Key(string): COS路径.
        :param kwargs(dict): 设置下载的headers.
        :return(dict): 下载成功返回的结果,包含Body对应的StreamBody,可以获取文件流或下载文件到本地.
        """
        headers = mapped(kwargs)
        url = self._conf.uri(bucket=Bucket, path=quote(Key, '/-_.~'))
        logger.info("get object, url=:{url} ,headers=:{headers}".format(
            url=url,
            headers=headers))
        rt = self.send_request(
                method='GET',
                url=url,
                stream=True,
                auth=CosS3Auth(self._conf._access_id, self._conf._access_key, Key),
                headers=headers)

        response = rt.headers
        response['Body'] = StreamBody(rt)

        return response

    def get_presigned_download_url(self, Bucket, Key, Expired=300):
        """生成预签名的下载url

        :param Bucket(string): 存储桶名称.
        :param Key(string): COS路径.
        :param Expired(int): 签名过期时间.
        :return(string): 预先签名的下载URL.
        """
        url = self._conf.uri(bucket=Bucket, path=quote(Key, '/-_.~'))
        sign = self.get_auth(Method='GET', Bucket=Bucket, Key=Key, Expired=300)
        url = url + '?sign=' + urllib.quote(sign)
        return url

    def delete_object(self, Bucket, Key, **kwargs):
        """单文件删除接口

        :param Bucket(string): 存储桶名称.
        :param Key(string): COS路径.
        :param kwargs(dict): 设置请求headers.
        :return: None.
        """
        headers = mapped(kwargs)
        url = self._conf.uri(bucket=Bucket, path=quote(Key, '/-_.~'))
        logger.info("delete object, url=:{url} ,headers=:{headers}".format(
            url=url,
            headers=headers))
        rt = self.send_request(
                method='DELETE',
                url=url,
                auth=CosS3Auth(self._conf._access_id, self._conf._access_key, Key),
                headers=headers)
        return None

    def head_object(self, Bucket, Key, **kwargs):
        """获取文件信息

        :param Bucket(string): 存储桶名称.
        :param Key(string): COS路径.
        :param kwargs(dict): 设置请求headers.
        :return(dict): 文件的metadata信息.
        """
        headers = mapped(kwargs)
        url = self._conf.uri(bucket=Bucket, path=quote(Key, '/-_.~'))
        logger.info("head object, url=:{url} ,headers=:{headers}".format(
            url=url,
            headers=headers))
        rt = self.send_request(
            method='HEAD',
            url=url,
            auth=CosS3Auth(self._conf._access_id, self._conf._access_key, Key),
            headers=headers)
        return rt.headers

    def gen_copy_source_url(self, CopySource):
        """拼接拷贝源url"""
        if 'Appid' in CopySource.keys():
            appid = CopySource['Appid']
        else:
            raise CosClientError('CopySource Need Parameter Appid')
        if 'Bucket' in CopySource.keys():
            bucket = CopySource['Bucket']
        else:
            raise CosClientError('CopySource Need Parameter Bucket')
        if 'Region' in CopySource.keys():
            region = CopySource['Region']
            region = format_region(region)
        else:
            raise CosClientError('CopySource Need Parameter Region')
        if 'Key' in CopySource.keys():
            path = CopySource['Key']
            if path and path[0] == '/':
                path = path[1:]
        else:
            raise CosClientError('CopySource Need Parameter Key')
        url = "{bucket}-{uid}.{region}.myqcloud.com/{path}".format(
                bucket=bucket,
                uid=appid,
                region=region,
                path=path
            )
        return url

    def copy_object(self, Bucket, Key, CopySource, CopyStatus='Copy', **kwargs):
        """文件拷贝，文件信息修改

        :param Bucket(string): 存储桶名称.
        :param Key(string): 上传COS路径.
        :param CopySource(dict): 拷贝源,包含Appid,Bucket,Region,Key.
        :param CopyStatus(string): 拷贝状态,可选值'Copy'|'Replaced'.
        :param kwargs(dict): 设置请求headers.
        :return(dict): 拷贝成功的结果.
        """
        headers = mapped(kwargs)
        if 'Metadata' in headers.keys():
            for i in headers['Metadata'].keys():
                headers[i] = headers['Metadata'][i]
            headers.pop('Metadata')
        headers['x-cos-copy-source'] = self.gen_copy_source_url(CopySource)
        if CopyStatus != 'Copy' and CopyStatus != 'Replaced':
            raise CosClientError('CopyStatus must be Copy or Replaced')
        headers['x-cos-metadata-directive'] = CopyStatus
        url = self._conf.uri(bucket=Bucket, path=quote(Key, '/-_.~'))
        logger.info("copy object, url=:{url} ,headers=:{headers}".format(
            url=url,
            headers=headers))
        rt = self.send_request(
            method='PUT',
            url=url,
            auth=CosS3Auth(self._conf._access_id, self._conf._access_key, Key),
            headers=headers)
        data = xml_to_dict(rt.text)
        return data

    def create_multipart_upload(self, Bucket, Key, **kwargs):
        """创建分片上传，适用于大文件上传

        :param Bucket(string): 存储桶名称.
        :param Key(string): COS路径.
        :param kwargs(dict): 设置请求headers.
        :return(dict): 初始化分块上传返回的结果，包含UploadId等信息.
        """
        headers = mapped(kwargs)
        if 'Metadata' in headers.keys():
            for i in headers['Metadata'].keys():
                headers[i] = headers['Metadata'][i]
            headers.pop('Metadata')

        url = self._conf.uri(bucket=Bucket, path=quote(Key, '/-_.~')+"?uploads")
        logger.info("create multipart upload, url=:{url} ,headers=:{headers}".format(
            url=url,
            headers=headers))
        rt = self.send_request(
                method='POST',
                url=url,
                auth=CosS3Auth(self._conf._access_id, self._conf._access_key, Key),
                headers=headers)

        data = xml_to_dict(rt.text)
        return data

    def upload_part(self, Bucket, Key, Body, PartNumber, UploadId, **kwargs):
        """上传分片，单个大小不得超过5GB

        :param Bucket(string): 存储桶名称.
        :param Key(string): COS路径.
        :param Body(file|string): 上传分块的内容,可以为文件流或者字节流.
        :param PartNumber(int): 上传分块的编号.
        :param UploadId(string): 分块上传创建的UploadId.
        :param kwargs(dict): 设置请求headers.
        :return(dict): 上传成功返回的结果，包含单个分块ETag等信息.
        """
        headers = mapped(kwargs)
        url = self._conf.uri(bucket=Bucket, path=quote(Key, '/-_.~')+"?partNumber={PartNumber}&uploadId={UploadId}".format(
            PartNumber=PartNumber,
            UploadId=UploadId))
        logger.info("put object, url=:{url} ,headers=:{headers}".format(
            url=url,
            headers=headers))
        rt = self.send_request(
                method='PUT',
                url=url,
                headers=headers,
                auth=CosS3Auth(self._conf._access_id, self._conf._access_key, Key),
                data=Body)
        response = dict()
        response['ETag'] = rt.headers['ETag']
        return response

    def complete_multipart_upload(self, Bucket, Key, UploadId, MultipartUpload={}, **kwargs):
        """完成分片上传,除最后一块分块块大小必须大于等于1MB,否则会返回错误.

        :param Bucket(string): 存储桶名称.
        :param Key(string): COS路径.
        :param UploadId(string): 分块上传创建的UploadId.
        :param MultipartUpload(dict): 所有分块的信息,包含Etag和PartNumber.
        :param kwargs(dict): 设置请求headers.
        :return(dict): 上传成功返回的结果，包含整个文件的ETag等信息.
        """
        headers = mapped(kwargs)
        url = self._conf.uri(bucket=Bucket, path=quote(Key, '/-_.~')+"?uploadId={UploadId}".format(UploadId=UploadId))
        logger.info("complete multipart upload, url=:{url} ,headers=:{headers}".format(
            url=url,
            headers=headers))
        rt = self.send_request(
                method='POST',
                url=url,
                auth=CosS3Auth(self._conf._access_id, self._conf._access_key, Key),
                data=dict_to_xml(MultipartUpload),
                timeout=1200,  # 分片上传大文件的时间比较长，设置为20min
                headers=headers)
        data = xml_to_dict(rt.text)
        return data

    def abort_multipart_upload(self, Bucket, Key, UploadId, **kwargs):
        """放弃一个已经存在的分片上传任务，删除所有已经存在的分片.

        :param Bucket(string): 存储桶名称.
        :param Key(string): COS路径.
        :param UploadId(string): 分块上传创建的UploadId.
        :param kwargs(dict): 设置请求headers.
        :return: None.
        """
        headers = mapped(kwargs)
        url = self._conf.uri(bucket=Bucket, path=quote(Key, '/-_.~')+"?uploadId={UploadId}".format(UploadId=UploadId))
        logger.info("abort multipart upload, url=:{url} ,headers=:{headers}".format(
            url=url,
            headers=headers))
        rt = self.send_request(
                method='DELETE',
                url=url,
                auth=CosS3Auth(self._conf._access_id, self._conf._access_key, Key),
                headers=headers)
        return None

    def list_parts(self, Bucket, Key, UploadId, EncodingType='', MaxParts=1000, PartNumberMarker=0, **kwargs):
        """列出已上传的分片.

        :param Bucket(string): 存储桶名称.
        :param Key(string): COS路径.
        :param UploadId(string): 分块上传创建的UploadId.
        :param EncodingType(string): 设置返回结果编码方式,只能设置为url.
        :param MaxParts(int): 设置单次返回最大的分块数量,最大为1000.
        :param PartNumberMarker(int): 设置返回的开始处,从PartNumberMarker下一个分块开始列出.
        :param kwargs(dict): 设置请求headers.
        :return(dict): 分块的相关信息，包括Etag和PartNumber等信息.
        """
        headers = mapped(kwargs)
        params = {
            'uploadId': UploadId,
            'part-number-marker': PartNumberMarker,
            'max-parts': MaxParts}
        if EncodingType:
            if EncodingType != 'url':
                raise CosClientError('EncodingType must be url')
            params['encoding-type'] = EncodingType

        url = self._conf.uri(bucket=Bucket, path=quote(Key, '/-_.~'))
        logger.info("list multipart upload, url=:{url} ,headers=:{headers}".format(
            url=url,
            headers=headers))
        rt = self.send_request(
                method='GET',
                url=url,
                auth=CosS3Auth(self._conf._access_id, self._conf._access_key, Key),
                headers=headers,
                params=params)
        data = xml_to_dict(rt.text)
        if 'Part' in data.keys() and isinstance(data['Part'], dict):  # 只有一个part，将dict转为list，保持一致
            lst = []
            lst.append(data['Part'])
            data['Part'] = lst
        return data

    def put_object_acl(self, Bucket, Key, AccessControlPolicy={}, **kwargs):
        """设置object ACL

        :param Bucket(string): 存储桶名称.
        :param Key(string): COS路径.
        :param AccessControlPolicy(dict): 设置object ACL规则.
        :param kwargs(dict): 通过headers来设置ACL.
        :return: None.
        """
        lst = [  # 类型为list的标签
            '<Grant>',
            '</Grant>']
        xml_config = ""
        if AccessControlPolicy:
            xml_config = format_xml(data=AccessControlPolicy, root='AccessControlPolicy', lst=lst)
        headers = mapped(kwargs)
        url = self._conf.uri(bucket=Bucket, path=quote(Key, '/-_.~')+"?acl")
        logger.info("put object acl, url=:{url} ,headers=:{headers}".format(
            url=url,
            headers=headers))
        rt = self.send_request(
            method='PUT',
            url=url,
            data=xml_config,
            auth=CosS3Auth(self._conf._access_id, self._conf._access_key, Key),
            headers=headers)
        return None

    def get_object_acl(self, Bucket, Key, **kwargs):
        """获取object ACL

        :param Bucket(string): 存储桶名称.
        :param Key(string): COS路径.
        :param kwargs(dict): 设置请求headers.
        :return(dict): Object对应的ACL信息.
        """
        headers = mapped(kwargs)
        url = self._conf.uri(bucket=Bucket, path=quote(Key, '/-_.~')+"?acl")
        logger.info("get object acl, url=:{url} ,headers=:{headers}".format(
            url=url,
            headers=headers))
        rt = self.send_request(
            method='GET',
            url=url,
            auth=CosS3Auth(self._conf._access_id, self._conf._access_key, Key),
            headers=headers)
        data = xml_to_dict(rt.text, "type", "Type")
        if data['AccessControlList'] is not None and isinstance(data['AccessControlList']['Grant'], dict):
            lst = []
            lst.append(data['AccessControlList']['Grant'])
            data['AccessControlList']['Grant'] = lst
        return data

    # s3 bucket interface begin
    def create_bucket(self, Bucket, **kwargs):
        """创建一个bucket

        :param Bucket(string): 存储桶名称.
        :param kwargs(dict): 设置请求headers.
        :return: None.
        """
        headers = mapped(kwargs)
        url = self._conf.uri(bucket=Bucket)
        logger.info("create bucket, url=:{url} ,headers=:{headers}".format(
            url=url,
            headers=headers))
        rt = self.send_request(
                method='PUT',
                url=url,
                auth=CosS3Auth(self._conf._access_id, self._conf._access_key),
                headers=headers)
        return None

    def delete_bucket(self, Bucket, **kwargs):
        """删除一个bucket，bucket必须为空

        :param Bucket(string): 存储桶名称.
        :param kwargs(dict): 设置请求headers.
        :return: None.
        """
        headers = mapped(kwargs)
        url = self._conf.uri(bucket=Bucket)
        logger.info("delete bucket, url=:{url} ,headers=:{headers}".format(
            url=url,
            headers=headers))
        rt = self.send_request(
                method='DELETE',
                url=url,
                auth=CosS3Auth(self._conf._access_id, self._conf._access_key),
                headers=headers)
        return None

    def list_objects(self, Bucket, Delimiter="", Marker="", MaxKeys=1000, Prefix="", EncodingType="", **kwargs):
        """获取文件列表

        :param Bucket(string): 存储桶名称.
        :param Delimiter(string): 分隔符.
        :param Marker(string): 从marker开始列出条目.
        :param MaxKeys(int): 设置单次返回最大的数量,最大为1000.
        :param Prefix(string): 设置匹配文件的前缀.
        :param EncodingType(string): 设置返回结果编码方式,只能设置为url.
        :param kwargs(dict): 设置请求headers.
        :return(dict): 文件的相关信息，包括Etag等信息.
        """
        headers = mapped(kwargs)
        url = self._conf.uri(bucket=Bucket)
        logger.info("list objects, url=:{url} ,headers=:{headers}".format(
            url=url,
            headers=headers))
        params = {
            'delimiter': Delimiter,
            'marker': Marker,
            'max-keys': MaxKeys,
            'prefix': Prefix
            }
        if EncodingType:
            if EncodingType != 'url':
                raise CosClientError('EncodingType must be url')
            params['encoding-type'] = EncodingType
        rt = self.send_request(
                method='GET',
                url=url,
                params=params,
                headers=headers,
                auth=CosS3Auth(self._conf._access_id, self._conf._access_key))

        data = xml_to_dict(rt.text)
        if 'Contents' in data.keys() and isinstance(data['Contents'], dict):  # 只有一个Contents，将dict转为list，保持一致
                lst = []
                lst.append(data['Contents'])
                data['Contents'] = lst
        return data

    def head_bucket(self, Bucket, **kwargs):
        """确认bucket是否存在

        :param Bucket(string): 存储桶名称.
        :param kwargs(dict): 设置请求headers.
        :return: None.
        """
        headers = mapped(kwargs)
        url = self._conf.uri(bucket=Bucket)
        logger.info("head bucket, url=:{url} ,headers=:{headers}".format(
            url=url,
            headers=headers))
        rt = self.send_request(
            method='HEAD',
            url=url,
            auth=CosS3Auth(self._conf._access_id, self._conf._access_key),
            headers=headers)
        return None

    def put_bucket_acl(self, Bucket, AccessControlPolicy={}, **kwargs):
        """设置bucket ACL

        :param Bucket(string): 存储桶名称.
        :param AccessControlPolicy(dict): 设置bucket ACL规则.
        :param kwargs(dict): 通过headers来设置ACL.
        :return: None.
        """
        lst = [  # 类型为list的标签
            '<Grant>',
            '</Grant>']
        xml_config = ""
        if AccessControlPolicy:
            xml_config = format_xml(data=AccessControlPolicy, root='AccessControlPolicy', lst=lst)
        headers = mapped(kwargs)
        url = self._conf.uri(bucket=Bucket, path="?acl")
        logger.info("put bucket acl, url=:{url} ,headers=:{headers}".format(
            url=url,
            headers=headers))
        rt = self.send_request(
            method='PUT',
            url=url,
            data=xml_config,
            auth=CosS3Auth(self._conf._access_id, self._conf._access_key),
            headers=headers)
        return None

    def get_bucket_acl(self, Bucket, **kwargs):
        """获取bucket ACL

        :param Bucket(string): 存储桶名称.
        :param kwargs(dict): 设置headers.
        :return(dict): Bucket对应的ACL信息.
        """
        headers = mapped(kwargs)
        url = self._conf.uri(bucket=Bucket, path="?acl")
        logger.info("get bucket acl, url=:{url} ,headers=:{headers}".format(
            url=url,
            headers=headers))
        rt = self.send_request(
            method='GET',
            url=url,
            auth=CosS3Auth(self._conf._access_id, self._conf._access_key),
            headers=headers)
        data = xml_to_dict(rt.text, "type", "Type")
        if data['AccessControlList'] is not None and not isinstance(data['AccessControlList']['Grant'], list):
            lst = []
            lst.append(data['AccessControlList']['Grant'])
            data['AccessControlList']['Grant'] = lst
        return data

    def put_bucket_cors(self, Bucket, CORSConfiguration={}, **kwargs):
        """设置bucket CORS

        :param Bucket(string): 存储桶名称.
        :param CORSConfiguration(dict): 设置Bucket跨域规则.
        :param kwargs(dict): 设置请求headers.
        :return: None.
        """
        lst = [  # 类型为list的标签
            '<CORSRule>',
            '<AllowedOrigin>',
            '<AllowedMethod>',
            '<AllowedHeader>',
            '<ExposeHeader>',
            '</CORSRule>',
            '</AllowedOrigin>',
            '</AllowedMethod>',
            '</AllowedHeader>',
            '</ExposeHeader>']
        xml_config = format_xml(data=CORSConfiguration, root='CORSConfiguration', lst=lst)
        headers = mapped(kwargs)
        headers['Content-MD5'] = get_md5(xml_config)
        headers['Content-Type'] = 'application/xml'
        url = self._conf.uri(bucket=Bucket, path="?cors")
        logger.info("put bucket cors, url=:{url} ,headers=:{headers}".format(
            url=url,
            headers=headers))
        rt = self.send_request(
            method='PUT',
            url=url,
            data=xml_config,
            auth=CosS3Auth(self._conf._access_id, self._conf._access_key),
            headers=headers)
        return None

    def get_bucket_cors(self, Bucket, **kwargs):
        """获取bucket CORS
        :param Bucket(string): 存储桶名称.
        :param kwargs(dict): 设置请求headers.
        :return(dict): 获取Bucket对应的跨域配置.
        """
        headers = mapped(kwargs)
        url = self._conf.uri(bucket=Bucket, path="?cors")
        logger.info("get bucket cors, url=:{url} ,headers=:{headers}".format(
            url=url,
            headers=headers))
        rt = self.send_request(
            method='GET',
            url=url,
            auth=CosS3Auth(self._conf._access_id, self._conf._access_key),
            headers=headers)
        data = xml_to_dict(rt.text)
        if 'CORSRule' in data.keys() and not isinstance(data['CORSRule'], list):
            lst = []
            lst.append(data['CORSRule'])
            data['CORSRule'] = lst
        if 'CORSRule' in data.keys():
            allow_lst = ['AllowedOrigin', 'AllowedMethod', 'AllowedHeader', 'ExposeHeader']
            for rule in data['CORSRule']:
                for text in allow_lst:
                    if text in rule.keys() and not isinstance(rule[text], list):
                        lst = []
                        lst.append(rule[text])
                        rule[text] = lst
        return data

    def delete_bucket_cors(self, Bucket, **kwargs):
        """删除bucket CORS

        :param Bucket(string): 存储桶名称.
        :param kwargs(dict): 设置请求headers.
        :return: None.
        """
        headers = mapped(kwargs)
        url = self._conf.uri(bucket=Bucket, path="?cors")
        logger.info("delete bucket cors, url=:{url} ,headers=:{headers}".format(
            url=url,
            headers=headers))
        rt = self.send_request(
            method='DELETE',
            url=url,
            auth=CosS3Auth(self._conf._access_id, self._conf._access_key),
            headers=headers)
        return None

    def put_bucket_lifecycle(self, Bucket, LifecycleConfiguration={}, **kwargs):
        """设置bucket LifeCycle
        :param Bucket(string): 存储桶名称.
        :param LifecycleConfiguration(dict): 设置Bucket的生命周期规则.
        :param kwargs(dict): 设置请求headers.
        :return: None.
        """
        lst = ['<Rule>', '<Tag>', '</Tag>', '</Rule>']  # 类型为list的标签
        xml_config = format_xml(data=LifecycleConfiguration, root='LifecycleConfiguration', lst=lst)
        headers = mapped(kwargs)
        headers['Content-MD5'] = get_md5(xml_config)
        headers['Content-Type'] = 'application/xml'
        url = self._conf.uri(bucket=Bucket, path="?lifecycle")
        logger.info("put bucket lifecycle, url=:{url} ,headers=:{headers}".format(
            url=url,
            headers=headers))
        rt = self.send_request(
            method='PUT',
            url=url,
            data=xml_config,
            auth=CosS3Auth(self._conf._access_id, self._conf._access_key),
            headers=headers)
        return None

    def get_bucket_lifecycle(self, Bucket, **kwargs):
        """获取bucket LifeCycle

        :param Bucket(string): 存储桶名称.
        :param kwargs(dict): 设置请求headers.
        :return(dict): Bucket对应的生命周期配置.
        """
        headers = mapped(kwargs)
        url = self._conf.uri(bucket=Bucket, path="?lifecycle")
        logger.info("get bucket cors, url=:{url} ,headers=:{headers}".format(
            url=url,
            headers=headers))
        rt = self.send_request(
            method='GET',
            url=url,
            auth=CosS3Auth(self._conf._access_id, self._conf._access_key),
            headers=headers)
        data = xml_to_dict(rt.text)
        if 'Rule' in data.keys() and not isinstance(data['Rule'], list):
            lst = []
            lst.append(data['Rule'])
            data['Rule'] = lst
        return data

    def delete_bucket_lifecycle(self, Bucket, **kwargs):
        """删除bucket LifeCycle

        :param Bucket(string): 存储桶名称.
        :param kwargs(dict): 设置请求headers.
        :return: None.
        """
        headers = mapped(kwargs)
        url = self._conf.uri(bucket=Bucket, path="?lifecycle")
        logger.info("delete bucket cors, url=:{url} ,headers=:{headers}".format(
            url=url,
            headers=headers))
        rt = self.send_request(
            method='DELETE',
            url=url,
            auth=CosS3Auth(self._conf._access_id, self._conf._access_key),
            headers=headers)
        return None

    def put_bucket_versioning(self, Bucket, Status, **kwargs):
        """设置bucket版本控制
        :param Bucket(string): 存储桶名称.
        :param Status(string): 设置Bucket版本控制的状态，可选值为'Enabled'|'Suspended'.
        :param kwargs(dict): 设置请求headers.
        :return: None.
        """
        headers = mapped(kwargs)
        url = self._conf.uri(bucket=Bucket, path="?versioning")
        logger.info("put bucket versioning, url=:{url} ,headers=:{headers}".format(
            url=url,
            headers=headers))
        if Status != 'Enabled' and Status != 'Suspended':
            raise CosClientError('versioning status must be set to Enabled or Suspended!')
        config = dict()
        config['Status'] = Status
        xml_config = format_xml(data=config, root='VersioningConfiguration')
        rt = self.send_request(
            method='PUT',
            url=url,
            data=xml_config,
            auth=CosS3Auth(self._conf._access_id, self._conf._access_key),
            headers=headers)
        return None

    def get_bucket_versioning(self, Bucket, **kwargs):
        """查询bucket版本控制

        :param Bucket(string): 存储桶名称.
        :param kwargs(dict): 设置请求headers.
        :return(dict): 获取Bucket版本控制的配置.
        """
        headers = mapped(kwargs)
        url = self._conf.uri(bucket=Bucket, path="?versioning")
        logger.info("get bucket versioning, url=:{url} ,headers=:{headers}".format(
            url=url,
            headers=headers))
        rt = self.send_request(
            method='GET',
            url=url,
            auth=CosS3Auth(self._conf._access_id, self._conf._access_key),
            headers=headers)
        data = xml_to_dict(rt.text)
        return data

    def get_bucket_location(self, Bucket, **kwargs):
        """查询bucket所属地域

        :param Bucket(string): 存储桶名称.
        :param kwargs(dict): 设置请求headers.
        :return(dict): 存储桶的地域信息.
        """
        headers = mapped(kwargs)
        url = self._conf.uri(bucket=Bucket, path="?location")
        logger.info("get bucket location, url=:{url} ,headers=:{headers}".format(
            url=url,
            headers=headers))
        rt = self.send_request(
            method='GET',
            url=url,
            auth=CosS3Auth(self._conf._access_id, self._conf._access_key),
            headers=headers)
        root = xml.etree.ElementTree.fromstring(rt.text)
        data = dict()
        data['LocationConstraint'] = root.text
        return data

    # service interface begin
    def list_buckets(self, **kwargs):
        """列出所有bucket

        :return(dict): 账号下bucket相关信息.
        """
        headers = mapped(kwargs)
        url = 'http://service.cos.myqcloud.com/'
        rt = self.send_request(
                method='GET',
                url=url,
                headers=headers,
                auth=CosS3Auth(self._conf._access_id, self._conf._access_key),
                )
        data = xml_to_dict(rt.text)
        if data['Buckets'] is not None and not isinstance(data['Buckets']['Bucket'], list):
            lst = []
            lst.append(data['Buckets']['Bucket'])
            data['Buckets']['Bucket'] = lst
        return data

if __name__ == "__main__":
    pass
