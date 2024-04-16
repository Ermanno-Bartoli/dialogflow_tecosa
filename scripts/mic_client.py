#!/usr/bin/env python

#from google.cloud import speech_v1 as speech
#from google.cloud.speech_v1 import enums
#from google.cloud.speech_v1 import types
import pyaudio
import queue as Queue
import rospy
from std_msgs.msg import String

# Dialogflow
from google.cloud import dialogflow_v2beta1 as dialogflow
# import dialogflow_v2beta1
from google.cloud.dialogflow_v2beta1 import types as dialogflow_types
from google.cloud.dialogflow_v2beta1.types import AudioEncoding, OutputAudioEncoding

import google.api_core.exceptions
from uuid import uuid4
import utils
from MicrophoneStream import MicrophoneStream

class GspeechClient(object):
    def __init__(self):
        # Audio stream input setup
        FORMAT = pyaudio.paInt16
        CHANNELS = 1
        RATE = 16000
        self.CHUNK = 4096
        self.audio = pyaudio.PyAudio()
        self.stream = self.audio.open(format=FORMAT, channels=CHANNELS,
                                      rate=RATE, input=True,
                                      frames_per_buffer=self.CHUNK,
                                      stream_callback=self._get_data)
        self._buff = Queue.Queue()  # Buffer to hold audio data
        self.closed = False

        self._audio_config = dialogflow_types.InputAudioConfig(audio_encoding=dialogflow_types.AudioEncoding.AUDIO_ENCODING_LINEAR_16,
                                              language_code='en-US',
                                              sample_rate_hertz=16000)
        self.last_contexts = []
        # ROS Text Publisher
        text_topic = rospy.get_param('/text_topic', '/dialogflow_text')
        project_id = rospy.get_param('/dialogflow_client/project_id', 'my-project-id')
        session_id = str(uuid4())
        self._session_cli = dialogflow.SessionsClient()
        self._session = self._session_cli.session_path(project_id, session_id)
        self.text_pub = rospy.Publisher(text_topic + "/string_msg", String, queue_size=10)

    def _get_data(self, in_data, frame_count, time_info, status):
        """Daemon thread to continuously get audio data from the server and put
         it in a buffer.
        """
        # Uncomment this if you want to hear the audio being replayed.
        self._buff.put(in_data)
        return None, pyaudio.paContinue

    def _generator(self):
        """Generator function that continuously yields audio chunks from the
        buffer. Used to stream data to the Google Speech API Asynchronously.
        :return A streaming request with the audio data.
        First request carries config data per Dialogflow docs.
        :rtype: Iterator[:class:`StreamingDetectIntentRequest`]
        """
        # First message contains session, query_input, and params
        query_input = dialogflow_types.QueryInput(audio_config=self._audio_config)
        req = dialogflow_types.StreamingDetectIntentRequest(
                session=self._session,
                query_input=query_input,
        )
        yield req

        with MicrophoneStream() as stream:
            audio_generator = stream.generator()
            for content in audio_generator:
                yield dialogflow_types.StreamingDetectIntentRequest(input_audio=content)

    def _listen_print_loop(self, responses):
        """Iterates through server responses and prints them.
        The responses passed is a generator that will block until a response
        is provided by the server.
        Each response may contain multiple results, and each result may contain
        multiple alternatives; for details, see https://goo.gl/tjCPAU.  Here we
        print only the transcription for the top alternative of the top result.
        """
        print("here")
        try:
            for response in responses:
                print(f"response: {response.recognition_result.transcript}")
                self.text_pub.publish(data=response.recognition_result.transcript)
                #resp_list.append(response)
                rospy.logdebug(
                        'DF_CLIENT: Intermediate transcript: "{}".'.format(
                                response.recognition_result.transcript))
        except google.api_core.exceptions.Cancelled as c:
            rospy.logwarn("DF_CLIENT: Caught a Google API Client cancelled "
                          "exception. Check request format!:\n{}".format(c))
        except google.api_core.exceptions.Unknown as u:
            rospy.logwarn("DF_CLIENT: Unknown Exception Caught:\n{}".format(u))
        except google.api_core.exceptions.ServiceUnavailable:
            rospy.logwarn("DF_CLIENT: Deadline exceeded exception caught. The response "
                          "took too long or you aren't connected to the internet!")
        """ for response in responses:
            # If not a valid response, move on to next potential one
            if not response.results:
                continue

            # The `results` list is consecutive. For streaming, we only care about
            # the first result being considered, since once it's `is_final`, it
            # moves on to considering the next utterance.
            result = response.results[0]
            if not result.alternatives:
                continue

            # Display the transcription of the top alternative.
            transcript = result.alternatives[0].transcript

            # Parse the final utterance
            if result.is_final:
                rospy.loginfo("Google Speech result: {}".format(result))
                # Received data is Unicode, convert it to string
                transcript = transcript.encode('utf-8')
                # Strip the initial space, if any
                if transcript.startswith(' '):
                    transcript = transcript[1:]
                # Exit if needed
                if transcript.lower() == 'exit':
                    self.shutdown()
                # Send the rest of the sentence to topic
                self.text_pub.publish(transcript) """

    def gspeech_client(self):
        """Creates the Google Speech API client, configures it, and sends/gets
        audio/text data for parsing.
        """
        # Hack from Google Speech Python docs, very pythonic c:
        responses = self._session_cli.streaming_detect_intent(self._generator())
        print("got responses")
        self._listen_print_loop(responses)

    def shutdown(self):
        """Shut down as cleanly as possible"""
        rospy.loginfo("Shutting down")
        self.closed = True
        self._buff.put(None)
        self.stream.close()
        self.audio.terminate()
        exit()

    def start_client(self):
        """Entry function to start the client"""
        try:
            rospy.loginfo("Starting Google speech mic client")
            self.gspeech_client()
        except KeyboardInterrupt:
            self.shutdown()


if __name__ == '__main__':
    rospy.init_node('dialogflow_mic_client')
    g = GspeechClient()
    g.start_client()

