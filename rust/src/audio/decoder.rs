use symphonia::{core::{formats::{FormatOptions, FormatReader}, meta::MetadataOptions, io::{MediaSourceStream, MediaSource}, probe::Hint, audio::AudioBufferRef}, default};

use super::cpal_output::CpalOutput;

pub struct Decoder;

impl Decoder
{
    pub fn new() -> Self { Decoder { } }

    pub fn open_stream(&self, source:Box<dyn MediaSource>)
    {
        let mss = MediaSourceStream::new(source, Default::default());

        let format_options = FormatOptions { enable_gapless: true, ..Default::default() };
        let metadata_options:MetadataOptions = Default::default();

        match default::get_probe().format(&Hint::new(), mss, &format_options, &metadata_options)
        {
            Err(err) => panic!("ERR: Failed to probe source. {err}"),
            Ok(mut probed) => self.give_ouput(&mut probed.format)
        }
    }

    fn give_ouput(&self, reader:&mut Box<dyn FormatReader>)
    {
        let track = reader.default_track().unwrap();
        let track_id = track.id;

        let mut decoder = default::get_codecs().make(&track.codec_params, &Default::default()).unwrap();
        let mut cpal_output:Option<CpalOutput> = None;

        loop
        {
            let packet = match reader.next_packet()
            {
                Ok(packet) => packet,
                Err(_err) => break
            };

            if packet.track_id() != track_id { continue; }
            
            match decoder.decode(&packet)
            {
                Err(err) => panic!("ERR: Failed to decode sound. {err}"),
                Ok(decoded) => self.decode(&mut cpal_output, decoded)
            }
        }
    }

    fn decode(&self, cpal_output:&mut Option<CpalOutput>, decoded:AudioBufferRef)
    {
        if cpal_output.is_none()
        {
            let spec = *decoded.spec();
            let duration = decoded.capacity() as u64;
            cpal_output.replace(CpalOutput::build_stream(spec, duration));
        }

        if let Some(cpal_output) = cpal_output
        {
            cpal_output.write(decoded);
        }
    }
}