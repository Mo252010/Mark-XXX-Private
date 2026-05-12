import re
from pathlib import Path

_root = Path(__file__).resolve().parent
_main = _root / "main.py"

content = _main.read_text(encoding="utf-8")

correct_receive_audio = '''    async def _receive_audio(self):
        print("[ATLAS] 👂 Recv started")
        out_buf = []
        in_buf  = []

        try:
            async for response in self.session.receive():

                if response.data:
                    self.audio_in_queue.put_nowait(response.data)
                    
                # Handle nested audio in server_content (newer SDKs)
                if response.server_content and response.server_content.model_turn:
                    for part in response.server_content.model_turn.parts:
                        if part.inline_data:
                            self.audio_in_queue.put_nowait(part.inline_data.data)

                if response.server_content:
                    sc = response.server_content

                    if sc.input_transcription and sc.input_transcription.text:
                        txt = sc.input_transcription.text.strip()
                        if txt:
                            in_buf.append(txt)

                    if sc.output_transcription and sc.output_transcription.text:
                        txt = sc.output_transcription.text.strip()
                        if txt:
                            out_buf.append(txt)

                    if sc.turn_complete:
                        full_in  = ""
                        full_out = ""

                        if in_buf:
                            full_in = " ".join(in_buf).strip()
                            if full_in:
                                self.ui.write_log(f"You: {full_in}")
                        in_buf = []

                        if out_buf:
                            full_out = " ".join(out_buf).strip()
                            if full_out:
                                self.ui.write_log(f"Atlas: {full_out}")
                        out_buf = []

                        if full_in and len(full_in) > 5:
                            threading.Thread(
                                target=_update_memory_async,
                                args=(full_in, full_out),
                                daemon=True
                            ).start()

                if response.tool_call:
                    for fc in response.tool_call.function_calls:
                        print(f"[ATLAS] 📞 Tool call: {fc.name}")
                    fn_responses = await asyncio.gather(
                        *[self._execute_tool(fc) for fc in response.tool_call.function_calls]
                    )
                    await self.session.send_tool_response(
                        function_responses=list(fn_responses)
                    )

        except Exception as e:
            print(f"[ATLAS] ❌ Recv error: {e}")
            traceback.print_exc()
            raise
'''

new_content = re.sub(
    r'    async def _receive_audio\(self\):.*?    async def _play_audio\(self\):', 
    correct_receive_audio + '\n    async def _play_audio(self):', 
    content, 
    flags=re.DOTALL
)

_main.write_text(new_content, encoding="utf-8")
print("Indentation fixed.")
