// This file is a part of simple_audio
// Copyright (c) 2022-2023 Erikas Taroza <erikastaroza@gmail.com>
//
// This program is free software: you can redistribute it and/or
// modify it under the terms of the GNU Lesser General Public License as
// published by the Free Software Foundation, either version 3 of
// the License, or (at your option) any later version.
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of 
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
// See the GNU Lesser General Public License for more details.
//
// You should have received a copy of the GNU Lesser General Public License along with this program.
// If not, see <https://www.gnu.org/licenses/>.

use std::io::{Read, Seek};
use std::ops::Range;
use std::thread;
use std::sync::mpsc::{channel, Receiver, Sender};

use rangemap::RangeSet;
use reqwest::blocking::Client;
use symphonia::core::io::MediaSource;

use super::streamable::*;

// NOTE: Most of the implementation is the same as HttpStream.
// Most comments can be found in HttpStream. Only the HLS specific
// comments are included.

pub struct HlsStream
{
    /// A list of parts with their size and URL.
    urls:Vec<(Range<usize>, String)>,
    buffer:Vec<u8>,
    read_position:usize,
    downloaded:RangeSet<usize>,
    requested:RangeSet<usize>,
    receivers:Vec<(u128, Receiver<(usize, Vec<u8>)>)>
}

impl HlsStream
{
    pub fn new(url:String) -> Self
    {
        let mut urls = Vec::new();
        let mut total_size = 0;

        let file = Client::new().get(url)
            .send().unwrap().text().unwrap();

        for line in file.lines()
        {
            if !line.contains("http") { continue; }

            // Get the size of the part.
            let res = Client::new().head(line)
                .send()
                .unwrap();

            let header = res
                .headers().get("Content-Length")
                .unwrap();

            let size:usize = header
                .to_str()
                .unwrap()
                .parse()
                .unwrap();

            urls.push((total_size..total_size + size + 1, line.to_string()));
            total_size += size;
        }

        HlsStream
        {
            urls,
            buffer: vec![0; total_size],
            read_position: 0,
            downloaded: RangeSet::new(),
            requested: RangeSet::new(),
            receivers: Vec::new()
        }
    }
}

impl Streamable<Self> for HlsStream
{
    fn read_chunk(tx:Sender<(usize, Vec<u8>)>, url:String, start:usize, _:usize)
    {
        let chunk = Client::new().get(url)
            .send().unwrap().bytes().unwrap().to_vec();
        
        tx.send((start, chunk)).unwrap();
    }

    fn try_write_chunk(&mut self, should_buffer:bool)
    {
        let mut completed_downloads = Vec::new();

        for (id, rx) in &self.receivers
        {
            let result = if self.downloaded.is_empty() || should_buffer {
                rx.recv().ok()
            } else { rx.try_recv().ok() };

            match result
            {
                None => (),
                Some((position, chunk)) => {
                    let end = position + chunk.len();

                    // During testing, the buffer wasn't sized correctly.
                    // In case there is more valid data than the buffer
                    // can hold, then expand it to prevent errors.
                    if end > self.buffer.len() {
                        self.buffer.append(&mut vec![0; end - self.buffer.len()]);
                    }

                    if position != end {
                        self.buffer[position..end].copy_from_slice(chunk.as_slice());
                        self.downloaded.insert(position..end);
                    }

                    completed_downloads.push(*id);
                }
            }
        }

        self.receivers.retain(|(id, _)| !completed_downloads.contains(&id));
    }

    fn should_get_chunk(&self) -> (bool, usize)
    {
        let closest_range = self.downloaded.get(&self.read_position);

        if closest_range.is_none() {
            return (true, self.read_position);
        }

        let closest_range = closest_range.unwrap();
        let is_already_downloading = self.requested.contains(&(self.read_position + CHUNK_SIZE));
        let prefetch_pos = self.read_position + (CHUNK_SIZE * 2);

        let should_get_chunk = prefetch_pos >= closest_range.end
            && !is_already_downloading
            && closest_range.end != self.buffer.len();
        
        (should_get_chunk, closest_range.end)
    }
}

impl Read for HlsStream
{
    fn read(&mut self, buf: &mut [u8]) -> std::io::Result<usize>
    {
        if self.read_position >= self.buffer.len() {
            return Ok(0);
        }

        let read_max = (self.read_position + buf.len()).min(self.buffer.len());
        let (should_get_chunk, chunk_write_pos) = self.should_get_chunk();
        
        // println!("Read: read_pos[{}] read_max[{read_max}] buf[{}] write_pos[{chunk_write_pos}] download[{should_get_chunk}]", self.read_position, buf.len());
        if should_get_chunk
        {
            // Find the correct part by checking if its range contains the
            // `chunk_write_pos`.
            let (range, url) = self.urls.iter()
                .find(|(range, _)| range.contains(&chunk_write_pos))
                .unwrap();

            self.requested.insert(chunk_write_pos..chunk_write_pos + range.clone().count() + 1);

            let url = url.clone();
            let file_size = self.buffer.len();
            let (tx, rx) = channel();

            let id = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH)
                .unwrap().as_millis();
            self.receivers.push((id, rx));

            thread::spawn(move || {
                Self::read_chunk(tx, url, chunk_write_pos, file_size);
            });
        }

        let should_buffer = !self.downloaded.contains(&self.read_position);
        IS_STREAM_BUFFERING.store(should_buffer, std::sync::atomic::Ordering::SeqCst);
        self.try_write_chunk(should_buffer);

        let bytes = &self.buffer[self.read_position..read_max];
        buf[0..bytes.len()].copy_from_slice(bytes);

        self.read_position += bytes.len();
        Ok(bytes.len())
    }
}

impl Seek for HlsStream
{
    fn seek(&mut self, pos: std::io::SeekFrom) -> std::io::Result<u64>
    {
        let seek_position:usize = match pos
        {
            std::io::SeekFrom::Start(pos) => pos as usize,
            std::io::SeekFrom::Current(pos) => {
                let pos = self.read_position as i64 + pos;
                pos.try_into().map_err(|_| {
                    std::io::Error::new(
                        std::io::ErrorKind::InvalidInput, 
                        format!("Invalid seek: {pos}")
                    )
                })?
            },
            std::io::SeekFrom::End(pos) => {
                let pos = self.buffer.len() as i64 + pos;
                pos.try_into().map_err(|_| {
                    std::io::Error::new(
                        std::io::ErrorKind::InvalidInput, 
                        format!("Invalid seek: {pos}")
                    )
                })?
            },
        };

        if seek_position > self.buffer.len() {
            return Ok(self.read_position as u64);
        }

        // println!("Seeking: pos[{seek_position}] type[{pos:?}]");

        self.read_position = seek_position;

        Ok(seek_position as u64)
    }
}

unsafe impl Send for HlsStream {}
unsafe impl Sync for HlsStream {}

impl MediaSource for HlsStream
{
    fn is_seekable(&self) -> bool {
        true
    }

    fn byte_len(&self) -> Option<u64> {
        Some(self.buffer.len() as u64)
    }
}