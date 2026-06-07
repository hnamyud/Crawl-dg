# DGTS Crawler

Tool crawl dữ liệu từ `dgts.moj.gov.vn` và tự xuất file Excel một sheet, không cần file mẫu.

## Cài dependencies

```powershell
python -m pip install -r requirements.txt
```

## Chạy crawl

Mở UI cấu hình:

```powershell
python -m dgts_crawler.ui
```

Hoặc chạy nhanh trên Windows:

```powershell
.\run_ui.bat
```

UI có 4 tab:

- `Thông báo công khai việc đấu giá`
- `Danh sách thông báo lựa chọn tổ chức hành nghề đấu giá`
- `Danh sách thông báo kết quả lựa chọn tổ chức hành nghề đấu giá`
- `Lịch sử thay đổi`

Trong mỗi tab chỉ cần chọn khoảng ngày, giới hạn trang, `File lưu kết quả` và bấm crawl.
`Số bản ghi mỗi trang` trong UI mặc định là `100` để giảm số lần gọi phân trang. `Số luồng tải chi tiết` mặc định là `5` để tải chi tiết nhiều tin song song. Nút `Dừng và xuất file` sẽ ngừng nhận việc mới, đợi các request chi tiết đang chạy kết thúc, rồi lưu Excel bằng dữ liệu đã crawl được.
App chỉ cho một tab crawl tại một thời điểm để tránh nhiều luồng cùng gọi API và cùng ghi DB lịch sử. Nếu một tab đang chạy, tab khác sẽ báo chờ tab hiện tại hoàn tất hoặc dừng trước.

Mặc định tool cũng lưu lịch sử crawl vào SQLite tại `outputs\dgts_history.sqlite` để phát hiện tin mới, tin đổi nội dung, tin biến mất trong đúng khoảng ngày đang crawl, và tin xuất hiện lại. Tin chỉ được đánh dấu `MISSING` sau khi không thấy trong danh sách crawl và kiểm tra lại detail/API cũng không còn dữ liệu hợp lệ. Nếu bấm `Dừng và xuất file`, tool vẫn lưu các tin đã quét được nhưng không đánh dấu `MISSING` để tránh cảnh báo sai do crawl chưa hết phạm vi.

## Build EXE

```powershell
.\build_exe.bat
```

Output là một file chạy độc lập:

```text
dist\DGTSCrawler.exe
```

Crawl theo khoảng ngày công khai:

```powershell
python -m dgts_crawler --from 2026-06-01 --to 2026-06-05 --output outputs\dgts.xlsx
```

Tăng tốc CLI bằng cách tăng số bản ghi mỗi trang và số luồng tải chi tiết:

```powershell
python -m dgts_crawler --from 2026-06-01 --to 2026-06-05 --page-size 100 --detail-workers 5 --output outputs\dgts.xlsx
```

Chỉ định file SQLite lịch sử hoặc tắt lưu lịch sử:

```powershell
python -m dgts_crawler --from 2026-06-01 --to 2026-06-05 --history-db outputs\history.sqlite --output outputs\dgts.xlsx
python -m dgts_crawler --from 2026-06-01 --to 2026-06-05 --no-history --output outputs\dgts.xlsx
```

Crawl thử 1 trang, mỗi trang 10 bản ghi:

```powershell
python -m dgts_crawler --from 2026-06-01 --to 2026-06-05 --max-pages 1 --page-size 10 --output outputs\dgts_sample.xlsx
```

Crawl toàn bộ dữ liệu khớp bộ lọc ngày:

```powershell
python -m dgts_crawler --from 2026-06-01 --to 2026-06-05 --all --output outputs\dgts_all.xlsx
```

Crawl danh sách thông báo lựa chọn tổ chức hành nghề đấu giá:

```powershell
python -m dgts_crawler --notice-kind select-org --max-pages 1 --page-size 10 --output outputs\select_org_sample.xlsx
```

Với tab `Danh sách thông báo lựa chọn tổ chức hành nghề đấu giá`, tool lọc nhanh theo `Ngày công khai` có sẵn ở danh sách trước, dừng khi gặp bản ghi cũ hơn khoảng ngày cần crawl, rồi mới vào chi tiết các tin còn khớp để lấy đủ thông tin.

Crawl danh sách thông báo kết quả lựa chọn tổ chức hành nghề đấu giá:

```powershell
python -m dgts_crawler --notice-kind select-org-result --max-pages 1 --page-size 10 --output outputs\select_org_result_sample.xlsx
```

Nếu không truyền `--from` và `--to`, tool mặc định crawl 7 ngày gần nhất.

## Theo dõi thay đổi bằng SQLite

SQLite lưu 3 nhóm dữ liệu:

- `crawl_runs`: mỗi lần crawl, khoảng ngày, loại tin và số lượng event.
- `notice_current`: trạng thái mới nhất của từng tin theo khóa `notice_kind + notice_id`.
- `notice_history`: log append-only các event `NEW`, `CHANGED`, `MISSING`, `REAPPEARED`, `SUSPECT_REPOST`, `SAME_ASSET_NAME`.

Logic xử lý:

- Tin chưa từng thấy trong DB được ghi `NEW`.
- Tin đã có nhưng hash nội dung quan trọng thay đổi được ghi `CHANGED`.
- Tin cũ nằm trong khoảng ngày đang crawl, không còn xuất hiện trong danh sách và detail/API cũng không còn dữ liệu hợp lệ được ghi `MISSING`.
- Tin đang `MISSING` xuất hiện lại được ghi `REAPPEARED`, kể cả khi xuất hiện dưới ID mới nhưng có fingerprint tài sản khớp.
- Tin mới khác ID nhưng có fingerprint tài sản trùng tin cũ và khác ngày đăng được ghi thêm `SUSPECT_REPOST`. Fingerprint dùng nội dung tài sản đã normalize/sort cùng một số thông tin như chủ tài sản, tỉnh, giá khởi điểm và tiền đặt trước. Các bản trùng cùng ngày đăng được coi là duplicate nội bộ và không hiển thị trong danh sách nghi đăng lại.
- Tin mới khác ID có tên tài sản trùng tin cũ nhưng fingerprint khác được ghi `SAME_ASSET_NAME` để kiểm tra thủ công, không coi là cùng tin.
- `detail_url` không được tính vào hash thay đổi vì slug URL của DGTS có thể đổi theo tên tài sản, trong khi ID cuối URL mới là phần định danh chính.

Nếu crawl hai khoảng ngày có phần trùng nhau, tool chỉ kiểm tra `MISSING` trong khoảng ngày của lần crawl hiện tại. Ví dụ crawl `01/06-05/06`, sau đó crawl `04/06-06/06`, các tin ngày `01/06-03/06` không bị đánh `MISSING` chỉ vì không nằm trong lần crawl sau.

Tab `Lịch sử thay đổi` trong UI cho phép chọn file SQLite, lọc theo loại tin, lọc theo event, xem lịch sử theo từng trang 500 dòng và xuất danh sách lịch sử đang xem ra Excel. Với event `CHANGED`, cột `Chi tiết đổi` hiển thị giá trị cũ và mới của từng trường thay đổi; các cột `Cũ` và `Mới` tách riêng hai phía để so sánh nhanh hơn. Với `SUSPECT_REPOST`, cột `Nghi trùng ID` hiển thị ID tin cũ nghi là cùng tài sản.

## Cấu trúc workbook xuất ra

File tab `Thông báo công khai việc đấu giá` có một sheet `Danh sách` với các cột tự tạo:

- `STT`
- `Ngày đăng (Lần 1/Lần 2)`
- `Mã tài sản`
- `Tên chi tiết tài sản đấu giá`
- `Tỉnh/Thành phố`
- `Cơ quan có tài sản`
- `Giá khởi điểm`
- `Tiền đặt trước`
- `Ghi chú`
- `Ngày hết hạn nộp HS`
- `Đường dẫn chi tiết`

Cột `Ghi chú` ghi nhóm phân loại từ regex có trọng số. Dòng dữ liệu được tự căn chiều cao để đọc các nội dung dài dễ hơn.

File tab `Danh sách thông báo lựa chọn tổ chức hành nghề đấu giá` có các cột:

- `STT`
- `Ngày đăng`
- `Tên tài sản`
- `Cơ quan có tài sản`
- `Địa chỉ`
- `Số lượng`
- `Chất lượng`
- `Giá khởi điểm`
- `Thời gian tiếp nhận HS`
- `Thời gian kết thúc HS`
- `Địa chỉ tiếp nhận HS`
- `Thông tin liên hệ`
- `Đường dẫn chi tiết`

File tab `Danh sách thông báo kết quả lựa chọn tổ chức hành nghề đấu giá` có các cột:

- `STT`
- `Ngày đăng`
- `Tên tài sản`
- `Cơ quan có tài sản`
- `Địa chỉ`
- `Số lượng`
- `Chất lượng`
- `Giá khởi điểm`
- `Tổ chức được chọn`
- `Địa chỉ`
- `Thông tin liên hệ`
- `Đường dẫn chi tiết`

## Phân loại dữ liệu

Tool phân loại bằng regex có trọng số trên `fullname`, `propertyName`, `subPropertyName`, `propertyTypeName`, `org_name` và `propertyPlace`. Dữ liệu từ API chi tiết `propertyInfo` được merge vào bản ghi trước khi phân loại.

Các nhóm sheet:

- `Thanh lý`: phương tiện, phá dỡ, công cụ dụng cụ, tài sản cố định, vật liệu thải, phế liệu.
- `Đất đai`: quyền sử dụng đất, quyền thuê đất, thửa đất, tờ bản đồ.
- `Ngân hàng`
- `Thi hành án`
- `Công an`
- `Khoáng sản`
- `Điện lực`
- `Công ty`

## Kiểm thử

```powershell
python -m pytest -q
```
