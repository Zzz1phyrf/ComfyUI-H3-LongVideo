"""Single RH task transport. Credentials and result URLs are never persisted."""
import json
import logging
from pathlib import Path
from urllib.parse import urlsplit
import uuid

import requests
from ..rh_credentials import api_key


class RemoteError(RuntimeError):
    pass


class Rejected(RemoteError):
    pass


def validate_config(config):
    if config.get('base_url') not in ('https://www.runninghub.ai','https://www.runninghub.cn'):
        raise ValueError('服务地址须选择RunningHub官方国内站或海外站。')
    if not str(config.get('workflow_id','')).isdigit():
        raise ValueError('请填写你有权调用的RH工作流ID。')
    if config.get('instance_type') not in ('default','plus','ultra'):
        raise ValueError('RH实例类型无效。')


class Client:
    def __init__(self,config):
        validate_config(config)
        self.config = config
        self.key = api_key(config['base_url'])
        if not self.key:
            raise RemoteError('未配置RH API Key。请点击节点上的“RH API 设置”，选择对应站点并保存，然后重新检查配置。')
        self.headers = {'Authorization':'Bearer '+self.key}

    def _post(self,path,body,timeout=60):
        try:
            response = requests.post(self.config['base_url']+path,headers=self.headers,json=body,timeout=timeout)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data,dict):
                raise RemoteError('RH返回格式无效，应为JSON对象；创建结果如未确认，请核对原任务，不会自动重建。')
            return data
        except (requests.RequestException,ValueError):
            raise RemoteError('RH请求或返回内容未能确认。任务ID如已保存，继续时只查询该任务；不会自动重建。') from None

    def upload(self,path):
        try:
            with Path(path).open('rb') as stream:
                response = requests.post(self.config['base_url']+'/openapi/v2/media/upload/binary',
                    headers=self.headers,files={'file':(Path(path).name,stream)},timeout=180)
            response.raise_for_status()
            data = response.json()
            if not isinstance(data,dict):
                raise RemoteError('RH素材上传返回格式无效；尚未提交新的生成任务。')
            record = data.get('data')
            name = (record.get('filename') or record.get('fileName')) if isinstance(record,dict) else None
            if data.get('code') not in (0,200) or not isinstance(name,str) or not name.strip():
                raise RemoteError('RH素材上传被拒绝，请检查账号权限及文件类型。')
            return name
        except (requests.RequestException,ValueError):
            raise RemoteError('素材上传失败，尚未提交新的生成任务。请检查网络后继续。') from None

    def create(self,graph):
        seed_node=str(self.config.get('seed_node','270'))
        seed_field='noise_seed' if graph.get(seed_node,{}).get('class_type')=='SamplerCustom' else 'seed'
        data = self._post('/task/openapi/create',{
            'workflowId':self.config['workflow_id'],'workflow':json.dumps(graph,ensure_ascii=False),
            'nodeInfoList':[{'nodeId':seed_node,'fieldName':seed_field,'fieldValue':self.config['seed']}],
            'instanceType':self.config['instance_type'],'addMetadata':True,'apiKey':self.key},120)
        if data.get('code')!=0:
            raise Rejected('RH明确拒绝创建任务，请检查工作流权限、额度或节点配置后手动重试。')
        record = data.get('data')
        task_id = str(record.get('taskId') or '') if isinstance(record,dict) else ''
        if not task_id.isdigit():
            raise RemoteError('RH没有返回有效任务ID，创建结果未知；请到RH任务列表核对后绑定任务ID。')
        return task_id

    def workflow(self):
        data = self._post('/api/openapi/getJsonApiFormat',
            {'apiKey':self.key,'workflowId':self.config['workflow_id']})
        record = data.get('data')
        value = record.get('prompt') if isinstance(record,dict) else None
        if data.get('code') != 0 or not isinstance(value,(str,dict)):
            raise RemoteError('无法读取RH工作流，请检查ID、区域及访问权限。尚未提交生成任务。')
        try:
            graph = json.loads(value) if isinstance(value,str) else value
            if not isinstance(graph,dict):raise ValueError('RH workflow must be an object')
            return graph
        except ValueError:
            raise RemoteError('RH工作流不是有效API图；尚未提交生成任务。') from None

    def query(self,task_id):
        return self._post('/openapi/v2/query',{'taskId':str(task_id)})

    def download(self,url,target):
        if urlsplit(url).scheme!='https' or not urlsplit(url).hostname:
            raise RemoteError('RH输出地址格式无效。')
        target = Path(target)
        if target.is_file():
            return
        partial = target.with_name('.'+target.name+'.'+uuid.uuid4().hex+'.part')
        created = False
        try:
            with requests.get(url,stream=True,timeout=180) as response:
                response.raise_for_status()
                with partial.open('xb') as stream:
                    created = True
                    for block in response.iter_content(1024*1024):
                        stream.write(block)
            partial.replace(target)
        except requests.RequestException:
            raise RemoteError('RH结果下载失败。已保存任务ID，继续时只下载同一任务，不重复生成。') from None
        finally:
            if created:
                try:
                    partial.unlink(missing_ok=True)
                except OSError as exc:
                    logging.warning('[H3LongVideo] 无法清理本次下载残片（%s）：%s', type(exc).__name__, partial)
