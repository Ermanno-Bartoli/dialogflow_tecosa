#!/usr/bin/env python

# Dialogflow
from google.cloud import dialogflow_v2beta1 as dialogflow
# import dialogflow_v2beta1
from google.cloud.dialogflow_v2beta1 import types as dialogflow_types

from customer_interaction.srv import GetAnswer, GetAnswerResponse

# Now you can use dialogflow_types to access the types
Context = dialogflow_types.Context
EventInput = dialogflow_types.EventInput
InputAudioConfig = dialogflow_types.InputAudioConfig
OutputAudioConfig = dialogflow_types.OutputAudioConfig
QueryInput = dialogflow_types.QueryInput
QueryParameters = dialogflow_types.QueryParameters
StreamingDetectIntentRequest = dialogflow_types.StreamingDetectIntentRequest
TextInput = dialogflow_types.TextInput

#Import Dialogflowresult
from google.protobuf import struct_pb2
from dialogflow_ros.msg import *


from google.cloud.dialogflow_v2beta1.types import AudioEncoding, OutputAudioEncoding

import google.api_core.exceptions


import utils

from AudioServerStream import AudioServerStream
from MicrophoneStream import MicrophoneStream

# Python
import pyaudio
import signal

import time
from uuid import uuid4
from yaml import load, YAMLError
# ROS
import rospy
import rospkg
from std_msgs.msg import String
from dialogflow_ros.msg import *

# Use to convert Struct messages to JSON
# from google.protobuf.json_format import MessageToJson


def parameters_struct_to_msg(parameters):
    """Convert Dialogflow parameter (Google Struct) into ros msg
    :param parameters:
    :type parameters: struct_pb2.Struct
    :return: List of DF Param msgs or empty list
    :rtype: (list of DialogflowParameter) or None
    """
    if parameters.items():
        param_list = []
        for name, value in parameters.items():
            param = DialogflowParameter(param_name=str(name), value=[str(value)])
            param_list.append(param)
        return param_list
    else:
        return []


def params_msg_to_struct(parameters):
    """Create a DF compatible parameter dictionary
    :param parameters: DialogflowParameter message
    :type parameters: list(DialogflowParameter)
    :return: Parameters as a dictionary (Technically)
    :rtype: struct_pb2.Struct
    """
    google_struct = struct_pb2.Struct()
    for param in parameters:
        google_struct[param.param_name] = param.value
    return google_struct


def events_msg_to_struct(event, language_code='en-US'):
    """Convert ROS Event Msg to DF Event
    :param event: ROS Event Message
    :type event: DialogflowEvent
    :param language_code: Language code of event, default 'en-US'
    :type language_code: str
    :return: Dialogflow EventInput to send
    :rtype: EventInput
    """
    parameters = params_msg_to_struct(event.parameters)
    return EventInput(name=event.event_name,
                      parameters=parameters,
                      language_code=language_code)


def contexts_struct_to_msg(contexts):
    """Utility function that fills the context received from Dialogflow into
    the ROS msg.
    :param contexts: The output_context received from Dialogflow.
    :type contexts: Context
    :return: The ROS DialogflowContext msg.
    :rtype: DialogflowContext
    """
    context_list = []
    for context in contexts:
        df_context_msg = DialogflowContext()
        df_context_msg.name = str(context.name)
        df_context_msg.lifespan_count = int(context.lifespan_count)
        df_context_msg.parameters = parameters_struct_to_msg(context.parameters)
        context_list.append(df_context_msg)
    return context_list


def contexts_msg_to_struct(contexts):
    """Utility function that fills the context received from ROS into
    the Dialogflow msg.
    :param contexts: The output_context received from ROS.
    :type contexts: DialogflowContext
    :return: The Dialogflow Context.
    :rtype: Context
    """
    context_list = []
    for context in contexts:
        new_parameters = params_msg_to_struct(context.parameters)
        new_context = Context(name=context.name,
                              lifespan_count=context.lifespan_count,
                              parameters=new_parameters)
        context_list.append(new_context)
    return context_list


def create_query_parameters(contexts=None):
    """Creates a QueryParameter with contexts. Last contexts used if
    contexts is empty. No contexts if none found.
    :param contexts: The ROS DialogflowContext message
    :type contexts: list(DialogflowContext)
    :return: A Dialogflow query parameters object.
    :rtype: QueryParameters
    """
    # Create a context list is contexts are passed
    if contexts:
        rospy.logdebug("DF_CLIENT: Using the following contexts:\n{}".format(
                        print_context_parameters(contexts)))
        contexts = contexts_msg_to_struct(contexts)
        return QueryParameters(contexts=contexts)


def result_struct_to_msg(query_result):
        """Utility function that fills the result received from Dialogflow into
        the ROS msg.
        :param query_result: The query_result received from Dialogflow.
        :type query_result: QueryResult
        :return: The ROS DialogflowResult msg.
        :rtype: DialogflowResult
        """
        df_result_msg = DialogflowResult()
        df_result_msg.fulfillment_text = str(query_result.fulfillment_text)
        df_result_msg.query_text = str(query_result.query_text)
        df_result_msg.action = str(query_result.action)
        df_result_msg.parameters = parameters_struct_to_msg(
                query_result.parameters
        )
        df_result_msg.contexts = contexts_struct_to_msg(
                query_result.output_contexts
        )
        df_result_msg.intent = str(query_result.intent.display_name)
        return df_result_msg

def print_context_parameters(contexts):
    result = []
    for context in contexts:
        param_list = []
        temp_str = '\n\t'
        for parameter in context.parameters:
            param_list.append("{}: {}".format(
                    parameter, context.parameters[parameter]))
        temp_str += "Name: {}\n\tParameters:\n\t {}".format(
                context.name.split('/')[-1], "\n\t".join(param_list))
        result.append(temp_str)
    result = "\n".join(result)
    return result


def print_parameters(parameters):
    param_list = []
    temp_str = '\n\t'
    for parameter in parameters:
        param_list.append("{}: {}\n\t".format(
                parameter, parameters[parameter]))
        temp_str += "{}".format("\n\t".join(param_list))
        return temp_str


def print_result(result):
    output = "DF_CLIENT: Results:\n" \
             "Query Text: {}\n" \
             "Detected intent: {} (Confidence: {})\n" \
             "Contexts: {}\n" \
             "Fulfillment text: {}\n" \
             "Action: {}\n" \
             "Parameters: {}".format(
                     result.query_text,
                     result.intent.display_name,
                     result.intent_detection_confidence,
                     print_context_parameters(result.output_contexts),
                     result.fulfillment_text,
                     result.action,
                     print_parameters(result.parameters))
    return output

class DialogflowClient(object):
    def __init__(self, language_code='en-US', last_contexts=None):
        """Initialize all params and load data"""
        """ Constants and params """
        self.CHUNK = 4096
        self.FORMAT = pyaudio.paInt16
        self.CHANNELS = 1
        self.RATE = 16000
        self.USE_AUDIO_SERVER = rospy.get_param('/dialogflow_client/use_audio_server', False)
        self.PLAY_AUDIO = rospy.get_param('/dialogflow_client/play_audio', False)
        self.DEBUG = rospy.get_param('/dialogflow_client/debug', False)

        # Register Ctrl-C sigint
        signal.signal(signal.SIGINT, self._signal_handler)

        """ Dialogflow setup """
        # Get hints/clues
        rp = rospkg.RosPack()
        file_dir = rp.get_path('dialogflow_ros') + '/config/context.yaml'
        with open(file_dir, 'r') as f:
            try:
                self.phrase_hints = load(f)
            except YAMLError:
                rospy.logwarn("DF_CLIENT: Unable to open phrase hints yaml file!")
                self.phrase_hints = []

        # Dialogflow params
        project_id = rospy.get_param('/dialogflow_client/project_id', 'my-project-id')
        session_id = str(uuid4())  # Random
        self._language_code = language_code
        self.last_contexts = last_contexts if last_contexts else []
        # DF Audio Setup
        audio_encoding = AudioEncoding.AUDIO_ENCODING_LINEAR_16
        # Possibel models: video, phone_call, command_and_search, default
        self._audio_config = InputAudioConfig(audio_encoding=audio_encoding,
                                              language_code=self._language_code,
                                              sample_rate_hertz=self.RATE,
                                              phrase_hints=self.phrase_hints,
                                              model='command_and_search')
        self._output_audio_config = OutputAudioConfig(
                audio_encoding=OutputAudioEncoding.OUTPUT_AUDIO_ENCODING_LINEAR_16
        )
        # Create a session
        self._session_cli = dialogflow.SessionsClient()
        self._session = self._session_cli.session_path(project_id, session_id)
        rospy.logdebug("DF_CLIENT: Session Path: {}".format(self._session))

        """ ROS Setup """
        results_topic = rospy.get_param('/dialogflow_client/results_topic',
                                        '/dialogflow_client/results')
        requests_topic = rospy.get_param('/dialogflow_client/requests_topic',
                                         '/dialogflow_client/requests')
        text_req_topic = requests_topic + '/string_msg'
        text_event_topic = requests_topic + '/string_event'
        msg_req_topic = requests_topic + '/df_msg'
        event_req_topic = requests_topic + '/df_event'
        self._results_pub = rospy.Publisher(results_topic, DialogflowResult,
                                            queue_size=10)
        rospy.Subscriber(text_req_topic, String, self._text_request_cb)
        rospy.Subscriber(text_event_topic, String, self._text_event_cb)
        rospy.Subscriber(msg_req_topic, DialogflowRequest, self._msg_request_cb)
        rospy.Subscriber(event_req_topic, DialogflowEvent, self._event_request_cb)
        self.text_pub = rospy.Publisher('/dialogflow_text', String, queue_size=10)

        """ Audio setup """
        # Mic stream input setup
        #self.audio = pyaudio.PyAudio()
        self._server_name = rospy.get_param('/dialogflow_client/server_name',
                                            '127.0.0.1')
        self._port = rospy.get_param('/dialogflow_client/port', 4444)

        if self.PLAY_AUDIO:
            self._create_audio_output()

        rospy.logdebug("DF_CLIENT: Last Contexts: {}".format(self.last_contexts))
        rospy.loginfo("DF_CLIENT: Ready!")

    # ========================================= #
    #           ROS Utility Functions           #
    # ========================================= #

    def _text_request_cb(self, msg):
        """ROS Callback that sends text received from a topic to Dialogflow,
        :param msg: A String message.
        :type msg: String
        """
        rospy.logdebug("DF_CLIENT: Request received")
        new_msg = DialogflowRequest(query_text=msg.data)
        df_msg = self.detect_intent_text(new_msg)

    def _msg_request_cb(self, msg):
        """ROS Callback that sends text received from a topic to Dialogflow,
        :param msg: A DialogflowRequest message.
        :type msg: DialogflowRequest
        """
        df_msg = self.detect_intent_text(msg)
        rospy.logdebug("DF_CLIENT: Request received:\n{}".format(df_msg))

    def _event_request_cb(self, msg):
        """
        :param msg: DialogflowEvent Message
        :type msg: DialogflowEvent"""
        new_event = events_msg_to_struct(msg)
        #self.event_intent(new_event)

    def _text_event_cb(self, msg):
        new_event = EventInput(name=msg.data, language_code=self._language_code)
        # self.event_intent(new_event)

    # ================================== #
    #           Setters/Getters          #
    # ================================== #

    def get_language_code(self):
        return self._language_code

    def set_language_code(self, language_code):
        assert isinstance(language_code, str), "Language code must be a string!"
        self._language_code = language_code

    # ==================================== #
    #           Utility Functions          #
    # ==================================== #

    def _signal_handler(self, signal, frame):
        rospy.logwarn("\nDF_CLIENT: SIGINT caught!")
        self.exit()

    # ----------------- #
    #  Audio Utilities  #
    # ----------------- #

    def _create_audio_output(self):
        """Creates a PyAudio output stream."""
        rospy.logdebug("DF_CLIENT: Creating audio output...")
        self.stream_out = self.audio.open(format=pyaudio.paInt16,
                                          channels=1,
                                          rate=24000,
                                          output=True)

    def _play_stream(self, data):
        """Simple function to play a the output Dialogflow response.
        :param data: Audio in bytes.
        """
        self.stream_out.start_stream()
        self.stream_out.write(data)
        time.sleep(0.2)  # Wait for stream to finish
        self.stream_out.stop_stream()

    # -------------- #
    #  DF Utilities  #
    # -------------- #

    def _generator(self):
        """Generator function that continuously yields audio chunks from the
        buffer. Used to stream data to the Google Speech API Asynchronously.
        :return A streaming request with the audio data.
        First request carries config data per Dialogflow docs.
        :rtype: Iterator[:class:`StreamingDetectIntentRequest`]
        """
        # First message contains session, query_input, and params
        query_input = QueryInput(audio_config=self._audio_config)
        contexts = contexts_msg_to_struct(self.last_contexts)
        params = QueryParameters(contexts=contexts)
        req = StreamingDetectIntentRequest(
                session=self._session,
                query_input=query_input,
                # query_params=params,
                # single_utterance=True,
                # output_audio_config=self._output_audio_config
        )
        yield req

        if self.USE_AUDIO_SERVER:
            with AudioServerStream() as stream:
                audio_generator = stream.generator()
                for content in audio_generator:
                    yield StreamingDetectIntentRequest(input_audio=content)
        else:
            with MicrophoneStream() as stream:
                audio_generator = stream.generator()
                for content in audio_generator:
                    yield StreamingDetectIntentRequest(input_audio=content)

    # ======================================== #
    #           Dialogflow Functions           #
    # ======================================== #



    def detect_intent_text(self, msg):
        print("msg is: ", msg)
        requests = self._generator()
        responses = self._session_cli.streaming_detect_intent(requests)
        for response in responses:
            if response.recognition_result:
                print("\r recognition_result:", response.recognition_result.transcript, end="")
                
            if response.query_result:
                print("query_result:", response.query_result)
            if response.recognition_result.is_final:
                print("----- FINAL -----")
                # publish on the topic /dialogflow_text
                self.text_pub.publish(data=response.recognition_result.transcript)
                return response.query_result
                                       
    def detect_intent_stream(self, msg):
        print("msg is: ", msg)

    def event_intent(self, event):
        """Send an event message to Dialogflow
        :param event: The ROS event message
        :type event: DialogflowEvent
        :return: The result from dialogflow as a ROS msg
        :rtype: DialogflowResult
        """
        # Convert if needed
        if type(event) is DialogflowEvent:
            event_input = events_msg_to_struct(event)
        else:
            event_input = event

        query_input = QueryInput(event=event_input)
        params = create_query_parameters(
                contexts=self.last_contexts
        )
        response = self._session_cli.detect_intent(
                session=self._session,
                query_input=query_input,
                # query_params=params,
                # output_audio_config=self._output_audio_config
        )
        df_msg = result_struct_to_msg(response.query_result)
        if self.PLAY_AUDIO:
            self._play_stream(response.output_audio)
        return df_msg

    def start(self):
        """Start the dialogflow client"""
        rospy.loginfo("DF_CLIENT: Spinning...")
        rospy.spin()

    def exit(self):
        """Close as cleanly as possible"""
        rospy.loginfo("DF_CLIENT: Shutting down")
        self.audio.terminate()
        exit()


def handle_get_status(req):
    print("Called by the service")
    return GetAnswerResponse(df.detect_intent_text(req))

if __name__ == '__main__':
    rospy.init_node('dialogflow_client')
    # Create a publisher to /dialogflow_text
    df = DialogflowClient()
    df.start()
    ##df.detect_intent_stream()
    #s = rospy.Service("get_answer", GetAnswer, handle_get_status)

