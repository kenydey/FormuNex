import { PageContainer, ProTable } from "@ant-design/pro-components";
import { App, Button, Form, Input, Popconfirm, Select, Space, Tabs, Typography } from "antd";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { call } from "@/api/client";
import { errorText } from "@/shared/feedback";

interface Substance { id: string; cas_no: string | null; name_zh: string; name_en: string }
interface Tox { id: string; endpoint: string; route: string; value: string; unit: string; species: string; method: string; source: string }
interface Eco { id: string; organism_group: string; endpoint: string; value: string; unit: string; duration: string; species: string; method: string; source: string }
interface Records { tox: Tox[]; eco: Eco[]; eco_profile: { persistence: string; bioaccum: string; mobility: string } | null }
interface Oel { standard: string; mac: number | null; pc_twa: number | null; pc_stel: number | null; critical_effect: string; notes: string }

const ROUTE_ZH: Record<string, string> = { oral: "经口", dermal: "经皮", inhalation: "吸入", other: "其他" };
const GROUP_ZH: Record<string, string> = { fish: "鱼类", daphnia: "水蚤", algae: "藻类" };

/** G6: measured tox / eco records per substance, feeding SDS sections 11/12. */
export default function TestRecordsPage() {
  const { message } = App.useApp();
  const qc = useQueryClient();
  const [q, setQ] = useState("");
  const [selected, setSelected] = useState<Substance | null>(null);
  const [toxForm] = Form.useForm();
  const [ecoForm] = Form.useForm();
  const [profForm] = Form.useForm();

  const subs = useQuery({ queryKey: ["substances", q], enabled: !!q,
    queryFn: () => call<Substance[]>("GET", `/api/plm/substances?q=${encodeURIComponent(q)}&limit=100`) });
  const recs = useQuery({ queryKey: ["test-records", selected?.id], enabled: !!selected,
    queryFn: () => call<Records>("GET", `/api/plm/substances/${selected!.id}/test-records`) });
  const oel = useQuery({ queryKey: ["oel", selected?.id], enabled: !!selected,
    queryFn: () => call<Oel[]>("GET", `/api/plm/substances/${selected!.id}/oel`) });

  const refresh = () => void qc.invalidateQueries({ queryKey: ["test-records", selected?.id] });
  const mut = useMutation({
    mutationFn: async (job: { method: string; path: string; body?: unknown }) => call(job.method, job.path, job.body),
    onSuccess: () => { message.success("已保存"); refresh(); },
    onError: (e) => message.error(errorText(e)),
  });
  const run = (method: string, path: string, body?: unknown) => mut.mutate({ method, path, body });

  return (
    <PageContainer title="试验记录" subTitle="物质级实测毒理 / 生态数据，SDS 第 11 / 12 节取数来源">
      <Input.Search placeholder="按 CAS 或中英文名称搜索物质" onSearch={setQ} style={{ marginBottom: 16 }} />
      <ProTable<Substance> rowKey="id" search={false} options={false} loading={subs.isFetching} dataSource={subs.data}
        onRow={(r) => ({ onClick: () => { setSelected(r); profForm.resetFields(); } })}
        rowClassName={(r) => (selected?.id === r.id ? "ant-table-row-selected" : "")}
        columns={[
          { title: "中文名", dataIndex: "name_zh" }, { title: "英文名", dataIndex: "name_en" },
          { title: "CAS", dataIndex: "cas_no" },
        ]} />
      {selected && recs.data && <div style={{ marginTop: 24 }}>
        <Typography.Title level={5}>{selected.name_zh || selected.name_en}（{selected.cas_no || "无 CAS"}）</Typography.Title>
        <Tabs items={[
          { key: "tox", label: "毒理记录", children: <>
            <ProTable<Tox> rowKey="id" search={false} options={false} dataSource={recs.data.tox} pagination={false}
              columns={[
                { title: "端点", dataIndex: "endpoint" }, { title: "途径", dataIndex: "route", render: (_, r) => ROUTE_ZH[r.route] ?? r.route },
                { title: "数值", dataIndex: "value" }, { title: "单位", dataIndex: "unit" }, { title: "物种", dataIndex: "species" },
                { title: "方法", dataIndex: "method" }, { title: "来源", dataIndex: "source" },
                { title: "操作", render: (_, r) => <Popconfirm title="删除该记录？" onConfirm={() => run("DELETE", `/api/plm/substances/${selected.id}/tox-records/${r.id}`)}><Button type="link" danger>删除</Button></Popconfirm> },
              ]} />
            <Form form={toxForm} layout="inline" style={{ marginTop: 12 }} onFinish={(v) => { run("POST", `/api/plm/substances/${selected.id}/tox-records`, v); toxForm.resetFields(); }}>
              <Form.Item name="endpoint" initialValue="LD50"><Select style={{ width: 90 }} options={["LD50", "LC50"].map((x) => ({ value: x, label: x }))} /></Form.Item>
              <Form.Item name="route" initialValue="oral"><Select style={{ width: 90 }} options={Object.entries(ROUTE_ZH).map(([value, label]) => ({ value, label }))} /></Form.Item>
              <Form.Item name="value" rules={[{ required: true, message: "数值必填" }]}><Input placeholder="数值，如 >2000" /></Form.Item>
              <Form.Item name="unit" initialValue="mg/kg"><Input placeholder="单位" style={{ width: 90 }} /></Form.Item>
              <Form.Item name="species" initialValue="大鼠"><Input placeholder="物种" style={{ width: 110 }} /></Form.Item>
              <Form.Item name="method"><Input placeholder="方法" /></Form.Item>
              <Form.Item><Button type="primary" htmlType="submit" loading={mut.isPending}>添加</Button></Form.Item>
            </Form>
          </> },
          { key: "eco", label: "生态记录", children: <>
            <ProTable<Eco> rowKey="id" search={false} options={false} dataSource={recs.data.eco} pagination={false}
              columns={[
                { title: "类群", dataIndex: "organism_group", render: (_, r) => GROUP_ZH[r.organism_group] ?? r.organism_group },
                { title: "端点", dataIndex: "endpoint" }, { title: "数值", dataIndex: "value" }, { title: "单位", dataIndex: "unit" },
                { title: "接触时间", dataIndex: "duration" }, { title: "物种", dataIndex: "species" }, { title: "方法", dataIndex: "method" },
                { title: "操作", render: (_, r) => <Popconfirm title="删除该记录？" onConfirm={() => run("DELETE", `/api/plm/substances/${selected.id}/eco-records/${r.id}`)}><Button type="link" danger>删除</Button></Popconfirm> },
              ]} />
            <Form form={ecoForm} layout="inline" style={{ marginTop: 12 }} onFinish={(v) => { run("POST", `/api/plm/substances/${selected.id}/eco-records`, v); ecoForm.resetFields(); }}>
              <Form.Item name="organism_group" rules={[{ required: true, message: "类群必填" }]}><Select placeholder="类群" style={{ width: 90 }} options={Object.entries(GROUP_ZH).map(([value, label]) => ({ value, label }))} /></Form.Item>
              <Form.Item name="endpoint" initialValue="LC50"><Select style={{ width: 90 }} options={["LC50", "EC50", "ErC50", "NOEC", "LOEC"].map((x) => ({ value: x, label: x }))} /></Form.Item>
              <Form.Item name="value" rules={[{ required: true, message: "数值必填" }]}><Input placeholder="数值" /></Form.Item>
              <Form.Item name="unit" initialValue="mg/L"><Input placeholder="单位" style={{ width: 90 }} /></Form.Item>
              <Form.Item name="duration"><Input placeholder="接触时间，如 96h" style={{ width: 120 }} /></Form.Item>
              <Form.Item name="species"><Input placeholder="物种" style={{ width: 110 }} /></Form.Item>
              <Form.Item name="method"><Input placeholder="方法" /></Form.Item>
              <Form.Item><Button type="primary" htmlType="submit" loading={mut.isPending}>添加</Button></Form.Item>
            </Form>
          </> },
          { key: "profile", label: "生态概况", children: <>
            <Form key={selected.id} form={profForm} layout="vertical" initialValues={recs.data.eco_profile ?? {}}
              onFinish={(v) => run("PUT", `/api/plm/substances/${selected.id}/eco-profile`, v)}>
              <Form.Item name="persistence" label="持久性和降解性"><Input.TextArea rows={2} /></Form.Item>
              <Form.Item name="bioaccum" label="潜在的生物累积性"><Input.TextArea rows={2} /></Form.Item>
              <Form.Item name="mobility" label="土壤中的迁移性"><Input.TextArea rows={2} /></Form.Item>
              <Form.Item><Space><Button type="primary" htmlType="submit" loading={mut.isPending}>保存</Button><span>至少填写一项</span></Space></Form.Item>
            </Form>
          </> },
          { key: "oel", label: "职业接触限值", children: <>
            <ProTable<Oel> rowKey="standard" search={false} options={false} dataSource={oel.data} pagination={false}
              columns={[
                { title: "标准", dataIndex: "standard" },
                { title: "MAC (mg/m³)", dataIndex: "mac", render: (_, r) => r.mac ?? "—" },
                { title: "PC-TWA (mg/m³)", dataIndex: "pc_twa", render: (_, r) => r.pc_twa ?? "—" },
                { title: "PC-STEL (mg/m³)", dataIndex: "pc_stel", render: (_, r) => r.pc_stel ?? "—" },
                { title: "关键效应", dataIndex: "critical_effect" },
                { title: "备注", dataIndex: "notes" },
              ]} />
            <div style={{ marginTop: 8 }}>GBZ 与 EH40 共表，标准列区分；EH40 的 ppm 值在备注中。</div>
          </> },
        ]} />
      </div>}
    </PageContainer>
  );
}
